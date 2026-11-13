"""The event-pairing benchmark template: its helpers, its gold set and its parity with the notebook-factory matcher."""

import ast
import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import nbformat
import numpy as np
import pytest
import responses
import yaml

from factory.events import disasters as D
from factory.notebooks.pipeline.inputs import RESERVED_PARAMETERS

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "notebook_templates" / "event-pairing-benchmark"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "montandon"

_spec = importlib.util.spec_from_file_location("event_pairing_benchmark", TEMPLATE / "pairing.py")
P = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = P
_spec.loader.exec_module(P)


def _rec(
    id_, source="usgs", start="2025-03-28", country="MMR", codes=("GH0101", "EQ"), point=(96.0, 22.0), **kw
):
    when = datetime.fromisoformat(start).replace(tzinfo=UTC)
    return P.Record(
        id=id_, source=source, title=kw.get("title", id_), text=kw.get("text", ""), start=when, end=when,
        countries=(country,), hazard_codes=tuple(codes), point=point,
        coarse=source in P.COARSE_SOURCES or point is None, magnitude=kw.get("magnitude"),
        corr_id=kw.get("corr_id", ""),
    )  # fmt: skip


# -- parity with factory/events/disasters.py ------------------------------------------------------------
@pytest.mark.parametrize("fixture", ["myanmar-eq-2025-window.json", "china-eq-2026-window.json"])
def test_heuristic_matches_the_notebook_factory_score_when_no_id_is_shared(fixture):
    items = json.loads((FIXTURES / fixture).read_text())["features"]
    members = [D.member_from_item(i) for i in items]
    records = [P.record_from_item(i) for i in items]
    compared = 0
    for anchor_m, anchor_r in zip(members, records, strict=True):
        for cand_m, cand_r in zip(members, records, strict=True):
            score, reason = D.score(anchor_m, cand_m)
            if cand_m.ref == anchor_m.ref or not (
                reason.endswith(" km") or reason.endswith("location unknown")
            ):
                continue  # shared ids decide those; the benchmark deliberately cannot see them
            assert P.heuristic_score(anchor_r, cand_r) == score, (anchor_m.ref, cand_m.ref)
            compared += 1
    assert compared > 20


def test_constants_match_the_app():
    assert (P.STRONG, P.POSSIBLE) == (D.STRONG, D.POSSIBLE)
    assert P.HAZARD_WINDOWS == D.HAZARD_WINDOWS and P.DEFAULT_WINDOW == D.DEFAULT_WINDOW
    assert P.COARSE_SOURCES == D.COARSE_SOURCES and P.COARSE_RADIUS_KM == D.COARSE_RADIUS_KM


# -- the gold set ----------------------------------------------------------------------------------------
def test_gold_set_is_well_formed():
    gold = yaml.safe_load((TEMPLATE / "gold_disasters.yml").read_text())
    ids = [m for d in gold["disasters"] for m in d["members"]]
    ambiguous = [a["id"] for a in gold["ambiguous"]]
    assert len(ids) == len(set(ids)), "an item is in two disasters"
    assert not set(ids) & set(ambiguous), "an item is both labelled and ambiguous"
    assert all(a["why"] for a in gold["ambiguous"])
    assert len({d["id"] for d in gold["disasters"]}) == len(gold["disasters"])
    assert all(d["members"] and d["window"] and d["name"] for d in gold["disasters"])
    assert all(P.collection_of(i).endswith("-events") for i in ids + ambiguous)
    label, windows, _ = P.gold_labels(gold)
    assert set(label) == set(ids) and set(windows.values()) >= {"tur-2023-02", "afg-2023-10"}


def test_collection_of():
    assert P.collection_of("emdat-event-2023-0054-TUR") == "emdat-events"
    assert P.collection_of("idmc-gidd-event-22194") == "idmc-gidd-events"
    assert P.collection_of("ifrcevent-event-6345") == "ifrcevent-events"
    assert P.collection_of("2023036S12117") == "ibtracs-events"


# -- candidates and pairs -------------------------------------------------------------------------------
def test_blocking_uses_country_and_time_and_ignores_unknown_country():
    a = _rec("a", country="MMR")
    assert P.block(a, _rec("b", country="CHL", point=(-70.6, -33.4)), 45) == "country"
    # across a border, 230 km away
    assert P.block(a, _rec("f", country="THA", point=(97.5, 20.5)), 45) == "ok"
    coarse_abroad = _rec("g", "emdat", country="THA", point=(100.9, 15.1))  # country centroid, 900 km away
    assert P.block(a, coarse_abroad, 45) == "ok"  # a country-level record is located loosely
    assert P.block(a, _rec("h", country="THA", point=None), 45) == "country"  # no point: the country decides
    assert P.block(a, _rec("c", start="2025-06-01"), 45) == "time"
    assert P.block(a, _rec("d", start="2025-03-30"), 45) == "ok"
    assert P.block(a, _rec("e", country="UNK"), 45) == "ok"  # a mislabelled country must not drop a match


def test_build_pairs_skips_same_family_and_other_windows():
    gold = {"disasters": [
        {"id": "x", "window": "w1", "kind": "control", "members": ["emdat-event-1", "usgs-event-1", "idmc-idu-event-1"]},
        {"id": "y", "window": "w1", "members": ["idmc-gidd-event-2", "glide-event-2"]},
        {"id": "z", "window": "w2", "members": ["pdc-event-3"]},
    ]}  # fmt: skip
    recs = {r.id: r for r in [
        _rec("emdat-event-1", "emdat"), _rec("usgs-event-1", "usgs"), _rec("idmc-idu-event-1", "idmc-idu"),
        _rec("idmc-gidd-event-2", "idmc-gidd"), _rec("glide-event-2", "glide"), _rec("pdc-event-3", "pdc"),
    ]}  # fmt: skip
    rows = P.build_pairs(recs, gold, 45)
    seen = {(r["a"], r["b"]): r["y"] for r in rows}
    assert not any("pdc-event-3" in k for k in seen)  # other window
    assert ("idmc-gidd-event-2", "idmc-idu-event-1") not in seen  # same organisation, one family
    assert seen[("emdat-event-1", "usgs-event-1")] == 1
    assert seen[("emdat-event-1", "glide-event-2")] == 0
    assert all(r["heuristic"] is not None for r in rows if r["block"] == "ok")


def test_hazard_relation():
    assert P.hazard_relation(_rec("a", codes=("GH0101",)), _rec("b", codes=("GH0101",))) == "same"
    assert P.hazard_relation(_rec("a", codes=("MH0600",)), _rec("b", codes=("MH0601",))) == "cluster"
    assert P.hazard_relation(_rec("a", codes=("MH0309",)), _rec("b", codes=("MH0600",))) == "cascade"
    assert P.hazard_relation(_rec("a", codes=("GH0101",)), _rec("b", codes=("EN0205",))) == "different"
    assert P.hazard_relation(_rec("a", codes=("EQ",)), _rec("b", codes=("EQ",))) == "unknown"


def test_pair_anchor_is_symmetric_and_prefers_the_precise_record():
    precise, coarse = _rec("u", "usgs"), _rec("e", "emdat")
    assert P.pair_anchor(precise, coarse)[0] is precise and P.pair_anchor(coarse, precise)[0] is precise
    early, late = _rec("a", "emdat", start="2025-03-01"), _rec("b", "glide", start="2025-03-05")
    assert P.pair_anchor(early, late)[0] is early and P.pair_anchor(late, early)[0] is early


# -- the other matching rules ----------------------------------------------------------------------------
def test_rule_baselines():
    a = _rec(
        "a",
        "emdat",
        start="2024-10-27",
        country="ESP",
        codes=("MH0600", "FL"),
        corr_id="20241027-ESP-1-MH0600-1-GCDB",
    )
    b = _rec(
        "b",
        "gdacs",
        start="2024-10-28",
        country="ESP",
        codes=("MH0600", "FL"),
        corr_id="20241028-ESP-2-MH0600-1-GCDB",
    )
    c = _rec(
        "c", "glide", start="2024-10-27", country="ESP", codes=("EQ",), corr_id="20241027-ESP-9-GH0101-1-GCDB"
    )
    d = _rec("d", "usgs", start="2024-11-02", country="ESP", codes=("FL",))
    assert (
        P.corr_id_equal(a, a) and not P.corr_id_equal(a, b) and not P.corr_id_equal(d, d)
    )  # no id, no match
    assert P.event_key_equal(a, c) and not P.event_key_equal(
        a, b
    )  # same date and country, whatever the hazard
    assert P.cql2_match(a, b, 1) and not P.cql2_match(a, c, 1)  # a hazard code must be shared
    assert not P.cql2_match(a, d, 1) and P.cql2_match(a, d, 7)  # the day window decides
    assert not P.cql2_match(a, _rec("e", country="THA", codes=("MH0600",)), 7)
    assert set(P.RULES) >= {"Rule: corr_id equal", "Rule: date + country"}


# -- metrics and the learned scorer ------------------------------------------------------------------------
def test_average_precision_and_prf():
    y = np.array([1, 1, 0, 0])
    assert P.average_precision(y, np.array([0.9, 0.8, 0.2, 0.1])) == pytest.approx(1.0)
    assert P.average_precision(y, np.array([0.1, 0.2, 0.8, 0.9])) < 0.5
    assert P.average_precision(y, np.full(4, 0.5)) == pytest.approx(0.5)  # ties do not depend on order
    assert P.prf(y, np.array([1, 0, 1, 0])) == pytest.approx((0.5, 0.5, 0.5))
    assert P.best_f1(y, np.array([0.9, 0.8, 0.2, 0.1]))[1] == pytest.approx(1.0)


def test_logreg_learns_a_separable_problem_and_cross_validation_runs():
    rng = np.random.default_rng(0)
    X = np.r_[rng.normal(-2, 1, (40, 3)), rng.normal(2, 1, (40, 3))]
    y = np.r_[np.zeros(40), np.ones(40)]
    model = P.fit_logreg(X, y, l2=1.0)
    assert ((model.predict(X) >= 0.5) == y.astype(bool)).mean() > 0.95
    groups = np.tile(np.arange(4), 20)
    oof = P.cross_val_logreg(X, y, groups)
    assert P.average_precision(y, oof) > 0.95
    one_class = P.cross_val_logreg(X[:10], y[:10], np.arange(10) % 2)  # training folds with a single class
    assert one_class.shape == (10,) and np.isfinite(one_class).all()


# -- the decision model ----------------------------------------------------------------------------------------
DOCUMENTED_RESPONSE = {  # shape from OpenRouter's Decisions API reference, with this template's question keys
    "answers": {
        "same_event": {"noul": 0.96, "type": "noul"},
        "relation": {
            "choice": "one_caused_other", "confidence": 0.75, "type": "choice",
            "probabilities": {"same_event": 0.1, "one_caused_other": 0.84, "unrelated": 0.06},
        },
    },
    "id": "gen-dec-1", "model": "typesafe/jev-1.14-20261014", "provider": "TypeSafe",
    "usage": {"cost": 0.00002, "input_tokens": 476, "output_tokens": 70},
}  # fmt: skip


def test_decision_request_carries_words_and_measured_facts_but_no_ids_or_codes(monkeypatch):
    monkeypatch.setattr(P, "LEAN_PROMPTS", False)  # the full prompt's structure
    a = _rec(
        "emdat-event-1",
        "emdat",
        start="2024-02-01",
        country="CHL",
        codes=("EN0205", "WF"),
        title="Wildfire in Chile",
    )
    b = _rec(
        "cems-event-1",
        "cems",
        start="2024-02-03",
        country="CHL",
        codes=("EN0205", "WF"),
        title="Valparaiso fire",
    )
    request = P.decision_request("typesafe/jev-1.14-20261014", a, b)
    assert request == P.decision_request(
        "typesafe/jev-1.14-20261014", b, a
    )  # the caller's order is irrelevant
    assert (
        set(request) == {"model", "state", "questions"} and request["model"] == "typesafe/jev-1.14-20261014"
    )
    state = request["state"]
    assert state["record_a"]["title"] == "Valparaiso fire"  # ordered by id: cems before emdat
    assert state["record_b"]["hazard"] == "wildfire" and state["record_b"]["countries"] == ["CHL"]
    assert state["record_b"]["kind"] == "EM-DAT disaster database record"
    assert state["days_apart"] == 2 and state["km_apart"] == 0
    text = json.dumps(state)
    assert "emdat-event-1" not in text and "EN0205" not in text  # no ids and no codes
    assert {q["type"] for q in request["questions"].values()} == {"noul", "choice"}


def test_parse_decision_and_gate():
    parsed = P.parse_decision(DOCUMENTED_RESPONSE)
    assert parsed["p_same"] == 0.96 and parsed["relation"] == "one_caused_other"
    assert (
        parsed["p_cascade"] == 0.84
        and parsed["model"] == "typesafe/jev-1.14-20261014"
        and parsed["cost"] == 0.00002
    )
    refusal = {"answers": {"same_event": {"type": "refusal", "message": "no"}}}
    assert P.parse_decision(refusal)["p_same"] is None
    assert [P.gate(p) for p in (0.9, 0.5, 0.1, None)] == ["same", "review", "different", "review"]


@responses.activate
def test_score_with_model_retries_throttling_and_reports_failures_instead_of_raising(monkeypatch):
    monkeypatch.setattr(P.time, "sleep", lambda _s: None)
    responses.add(responses.POST, P.DECISIONS_URL, status=429, json={"error": {"code": 429}})
    responses.add(responses.POST, P.DECISIONS_URL, status=200, json=DOCUMENTED_RESPONSE)
    pair = (_rec("a", "emdat"), _rec("b", "usgs"))
    (ok,) = P.score_with_model([pair], "typesafe/jev-1.14-20261014", "key", workers=1)
    assert ok["p_same"] == 0.96 and "error" not in ok
    assert responses.calls[-1].request.headers["Authorization"] == "Bearer key"

    responses.reset()
    responses.add(
        responses.POST, P.DECISIONS_URL, status=400, json={"error": {"code": 400, "message": "bad"}}
    )
    (failed,) = P.score_with_model([pair], "typesafe/jev-1.14-20261014", "key", workers=1)
    assert failed["p_same"] is None and "400" in failed["error"]


# -- pipeline stages: date gate and candidate ranking --------------------------------------------------------
def test_gate_and_margin_scores():
    scores = np.array([0.9, 0.6, 0.85, 0.95, np.nan])
    a = np.array(["a1", "a1", "a1", "a2", "a2"])
    b = np.array(["b1", "b2", "b3", "b4", "b5"])
    fam_a, fam_b = np.array(["x"] * 5), np.array(["y"] * 5)
    gated = P.gate_scores(scores, np.array([1, 10, 3, 8, 0]), gate_days=7)
    assert list(gated[:4]) == [0.9, 0.0, 0.85, 0.0] and np.isnan(
        gated[4]
    )  # beyond 7 days: settled as different
    ranked = P.margin_scores(scores, a, b, fam_a, fam_b, margin=0.15)
    assert list(ranked[:4]) == [
        0.9,
        0.0,
        0.85,
        0.95,
    ]  # 0.6 loses to 0.9; 0.85 stays (several matches per source)
    assert np.isnan(ranked[4])  # an unanswered pair is not turned into a verdict
    assert P.margin_scores(scores, a, b, fam_a, fam_b, margin=0.0)[2] == 0.0  # margin 0 is one-to-one


# -- the listwise decision call ------------------------------------------------------------------------------------
def test_listwise_request_names_candidates_and_carries_no_ids(monkeypatch):
    monkeypatch.setattr(P, "LEAN_PROMPTS", False)  # the full prompt's structure
    anchor = _rec(
        "emdat-event-1", "emdat", start="2023-10-07", country="AFG", title="Ground movement in Afghanistan"
    )
    cands = [
        _rec("usgs-event-b", "usgs", start="2023-10-07", country="AFG", title="M 6.3 - Herat"),
        _rec("usgs-event-c", "usgs", start="2023-10-11", country="AFG", title="M 6.3 - Herat"),
    ]
    request = P.listwise_request("typesafe/jev-1.14-20261014", anchor, cands)
    assert set(request) == {"model", "state", "questions"}
    assert list(request["state"]["candidates"]) == ["candidate_1", "candidate_2"]
    assert [c["days_apart"] for c in request["state"]["candidates"].values()] == [0, 4]
    q = request["questions"]
    assert set(q) == {"same_candidate_1", "same_candidate_2", "best", "any_match"}
    assert set(q["best"]["criteria"]) == {"candidate_1", "candidate_2"} and q["best"]["type"] == "choice"
    text = json.dumps(request["state"])
    assert "usgs-event-b" not in text and "emdat-event-1" not in text and "GH0101" not in text


def test_parse_listwise_and_pair_scores_use_both_directions():
    response = {
        "answers": {
            "same_candidate_1": {"type": "noul", "noul": 0.9},
            "same_candidate_2": {"type": "noul", "noul": 0.2},
            "best": {
                "type": "choice",
                "choice": "candidate_1",
                "probabilities": {"candidate_1": 0.8, "candidate_2": 0.2},
            },
            "any_match": {"type": "noul", "noul": 0.95},
        },
        "model": "typesafe/jev-1.14-20261014",
        "usage": {"cost": 0.00003},
    }
    parsed = P.parse_listwise(response, 2)
    assert parsed["p_same"] == [0.9, 0.2] and parsed["best"] == [0.8, 0.2] and parsed["any_match"] == 0.95
    refusal = {"answers": {"same_candidate_1": {"type": "refusal"}}}
    assert P.parse_listwise(refusal, 2)["p_same"] == [None, None]
    groups = [("a", ["b", "c"]), ("b", ["a"])]
    results = [parsed, {"p_same": [0.7], "best": [0.6]}]
    score, best = P.pair_scores_from_listwise(groups, results, [("a", "b"), ("a", "c"), ("b", "z")])
    assert (
        score[0] == pytest.approx(0.8) and score[1] == pytest.approx(0.2) and np.isnan(score[2])
    )  # mean of directions
    assert best[0] == pytest.approx(0.7)


def test_listwise_groups_one_per_record_and_family_with_distractors_and_chunking():
    recs = {i: _rec(i, s) for i, s in [("a", "emdat"), ("b", "usgs"), ("c", "usgs"), ("d", "gdacs")]}
    groups = P.listwise_groups([("a", "b"), ("a", "c"), ("a", "d")], recs)
    assert (
        ("a", ["b", "c"]) in groups and ("a", ["d"]) in groups and ("b", ["a"]) in groups and len(groups) == 5
    )
    with_extra = P.listwise_groups([("a", "b")], recs, extra={("a", "usgs"): ["x"], ("zz", "usgs"): ["y"]})
    assert ("a", ["b", "x"]) in with_extra  # distractors join an existing group only
    chunked = P.listwise_groups([("a", "b"), ("a", "c")], recs, max_candidates=1)
    assert [g for g in chunked if g[0] == "a"] == [("a", ["b"]), ("a", ["c"])]


def test_choose_distractors_takes_the_closest_rivals_of_the_right_source():
    anchor = _rec("emdat-event-1", "emdat", start="2024-10-27", country="ESP")
    context = {
        c.id: c
        for c in [
            _rec("usgs-near", "usgs", start="2024-10-27", country="ESP"),
            _rec("usgs-mid", "usgs", start="2024-10-30", country="ESP"),
            _rec("usgs-far", "usgs", start="2024-12-30", country="ESP"),  # beyond the gate
            _rec("usgs-abroad", "usgs", start="2024-10-27", country="CHL", point=(-70.6, -33.4)),
            _rec("gdacs-near", "gdacs", start="2024-10-27", country="ESP"),  # other source
        ]
    }
    chosen = P.choose_distractors(
        [("emdat-event-1", "usgs")], {anchor.id: anchor}, context, gate_days=7, per_group=5
    )
    assert chosen == {("emdat-event-1", "usgs"): ["usgs-near", "usgs-mid"]}
    assert P.choose_distractors([("emdat-event-1", "usgs")], {anchor.id: anchor}, context, 7, 1)[
        ("emdat-event-1", "usgs")
    ] == ["usgs-near"]


@responses.activate
def test_score_listwise_calls_once_per_group_and_reports_failures(monkeypatch):
    monkeypatch.setattr(P, "LEAN_PROMPTS", False)  # the full prompt's structure
    recs = {i: _rec(i, s) for i, s in [("a", "emdat"), ("b", "usgs"), ("c", "usgs")]}
    body = {
        "answers": {
            "same_candidate_1": {"type": "noul", "noul": 0.9},
            "same_candidate_2": {"type": "noul", "noul": 0.1},
            "best": {"type": "choice", "probabilities": {"candidate_1": 0.9, "candidate_2": 0.1}},
        }
    }
    responses.add(responses.POST, P.DECISIONS_URL, status=200, json=body)
    (res,) = P.score_listwise([("a", ["b", "c"])], recs, "typesafe/jev-1.14-20261014", "key", workers=1)
    assert res["p_same"] == [0.9, 0.1] and len(responses.calls) == 1
    sent = json.loads(responses.calls[0].request.body)
    assert set(sent["questions"]) == {"same_candidate_1", "same_candidate_2", "best", "any_match"}
    responses.reset()
    responses.add(responses.POST, P.DECISIONS_URL, status=400, json={"error": {"code": 400}})
    (failed,) = P.score_listwise([("a", ["b"])], recs, "typesafe/jev-1.14-20261014", "key", workers=1)
    assert failed["p_same"] == [None] and "400" in failed["error"]


# -- the template files -------------------------------------------------------------------------------------------
def test_notebook_parameters_match_template_yml():
    nb = nbformat.read(TEMPLATE / "notebook.ipynb", as_version=4)
    meta = yaml.safe_load((TEMPLATE / "template.yml").read_text())
    (cell,) = [c for c in nb.cells if "parameters" in c.metadata.get("tags", [])]
    names = {t.targets[0].id for t in ast.parse(cell.source).body if isinstance(t, ast.Assign)}
    assert names - set(RESERVED_PARAMETERS) == set(meta["parameters"]["properties"])
    assert meta["secrets"] == ["OPENROUTER_API_KEY"] and meta["requires_stac_item"] is True


# -- gold set against the sources' own references (offline snapshot) ------------------------------------------
GOLD_CHECKS = json.loads(
    (Path(__file__).resolve().parent / "fixtures" / "event_pairing" / "gold_checks.json").read_text()
)


def _gold():
    gold = yaml.safe_load((TEMPLATE / "gold_disasters.yml").read_text())
    return gold, P.gold_labels(gold)[0]


def test_gold_agrees_with_the_usgs_and_glide_references_gdacs_publishes():
    _, label = _gold()
    glide_members = {
        i.removeprefix("glide-event-"): d for i, d in label.items() if i.startswith("glide-event-")
    }
    checked = 0
    for gdacs_id, ref in GOLD_CHECKS["gdacs_refs"].items():
        if gdacs_id not in label:
            continue
        usgs = f"usgs-event-{ref['usgs']}"
        if ref["usgs"] and usgs in label:
            assert label[usgs] == label[gdacs_id], (gdacs_id, usgs)
            checked += 1
        if ref["glide"] in glide_members:
            assert glide_members[ref["glide"]] == label[gdacs_id], (gdacs_id, ref["glide"])
            checked += 1
    assert checked >= 15


def test_each_earthquake_disaster_holds_its_largest_shock():
    """The Tohoku error: a member alert that was an aftershock while the mainshock was left out."""
    _, label = _gold()

    def event(item_id: str) -> str:  # GDACS repeats an event once per episode
        return item_id.rsplit("-", 1)[0] if item_id.startswith("gdacs-") else item_id

    for disaster, shocks in GOLD_CHECKS["largest_shocks"].items():
        members = {event(i) for i, d in label.items() if d == disaster}
        for source in ("usgs", "gdacs"):
            rows = [s for s in shocks if s["source"] == source]
            if not rows or not any(event(i) in members for i in (s["id"] for s in rows)):
                continue  # the disaster has no member from this source
            top = max(s["magnitude"] for s in rows)
            assert members & {event(s["id"]) for s in rows if s["magnitude"] == top}, (disaster, source, top)


def test_gold_spans_countries():
    gold, label = _gold()
    countries = {
        d["id"]: {i.rsplit("-", 1)[1] for i in d["members"] if i.startswith("emdat-event-")}
        for d in gold["disasters"]
    }
    assert sum(len(c) > 1 for c in countries.values()) >= 4  # Türkiye/Syria, Freddy, Melissa, Haiyan


# -- scope, weights, uncertainty, confidence, events ----------------------------------------------------------
def test_destructive_means_a_record_from_an_impact_source():
    assert P.destructive(_rec("e", "emdat")) and P.destructive(_rec("i", "idmc-gidd"))
    assert not P.destructive(_rec("u", "usgs")) and not P.destructive(_rec("g", "gdacs"))


def test_copies_are_weighted_so_a_duplicated_pair_counts_once():
    recs = {
        "idmc-idu-event-1": _rec("idmc-idu-event-1", "idmc-idu", title="Flood - X"),
        "idmc-gidd-event-1": _rec("idmc-gidd-event-1", "idmc-gidd", title="Flood - X"),
        "usgs-1": _rec("usgs-1"),
    }
    label = dict.fromkeys(recs, "d")
    copies = P.copy_counts(recs, label)
    assert copies == {"idmc-idu-event-1": 2, "idmc-gidd-event-1": 2, "usgs-1": 1}
    w = P.pair_weights(["idmc-idu-event-1", "idmc-gidd-event-1"], ["usgs-1", "usgs-1"], copies)
    assert w.tolist() == [0.5, 0.5]
    y, pred = np.array([1, 1, 0]), np.array([True, False, True])
    assert P.prf(y, pred) == P.prf(y, pred, np.ones(3))
    assert P.prf(y, pred, np.array([1.0, 0.0, 1.0]))[1] == 1.0  # the missed pair weighs nothing
    assert P.average_precision(y, np.array([0.9, 0.1, 0.5]), np.ones(3)) == P.average_precision(
        y, np.array([0.9, 0.1, 0.5])
    )


def test_bootstrap_resamples_windows_and_pairs_scorers():
    y = np.array([1, 1, 0, 0, 1, 0, 1, 0])
    groups = np.array(["a", "a", "a", "b", "b", "c", "c", "c"])
    good, coin = y.astype(float), np.array([0.9, 0.2, 0.8, 0.1, 0.6, 0.7, 0.3, 0.4])
    samples = P.bootstrap_f1(
        y, {"good": (good, 0.5), "coin": (coin, 0.5), "same": (good, 0.5)}, groups, n=200
    )
    assert np.all(samples["good"] == 1.0)
    assert np.all(samples["good"] - samples["same"] == 0)  # identical scorers, identical resamples
    lo, hi = P.interval(samples["coin"])
    assert 0 <= lo <= P.prf(y, coin >= 0.5)[2] <= hi <= 1
    again = P.bootstrap_f1(y, {"coin": (coin, 0.5)}, groups, n=200)
    assert np.array_equal(again["coin"], samples["coin"])  # seeded


def test_calibration_table_and_brier():
    y = np.array([1, 1, 0, 0])
    assert P.brier(y, y.astype(float)) == 0.0
    assert P.brier(y, np.full(4, 0.5)) == 0.25
    assert P.brier(y, np.array([1.0, np.nan, 0.0, 0.0])) == 0.0  # unanswered pairs are left out
    rows = P.calibration_table(y, np.array([0.95, 0.85, 0.05, 0.15]), bins=5)
    assert [r["pairs"] for r in rows] == [2, 2]
    assert rows[0]["share same"] == 0.0 and rows[-1]["share same"] == 1.0


def test_assemble_events_and_bcubed():
    ids = ["a", "b", "c", "d", "e"]
    a, b, s = ["a", "b", "c", "d"], ["b", "c", "d", "e"], [0.9, 0.2, 0.8, np.nan]
    events = P.assemble_events(ids, a, b, s, 0.5)
    assert events["a"] == events["b"] and events["c"] == events["d"] and events["a"] != events["c"]
    assert len(set(events.values())) == 3  # e stays alone
    gold = {"a": "x", "b": "x", "c": "y", "d": "y", "e": "y"}
    p, r, _ = P.bcubed(events, gold)
    assert p == 1.0 and r == pytest.approx((1 + 1 + 2 / 3 + 2 / 3 + 1 / 3) / 5)
    merged = P.assemble_events(ids, a, b, [0.9, 0.9, 0.9, 0.9], 0.5)
    assert P.bcubed(merged, gold)[1] == 1.0 and P.bcubed(merged, gold)[0] < 1.0


def test_describe_adds_magnitude_and_end_date_but_still_no_ids(monkeypatch):
    rec = _rec(
        "usgs-event-us7000pn9s",
        magnitude=7.7,
        title="M 7.7 - Mandalay",
        corr_id="20250328-MMR-1-GH0101-1-GCDB",
    )
    d = P.describe(rec)
    assert d["magnitude"] == 7.7 and d["ends"] == "2025-03-28"
    assert "us7000pn9s" not in json.dumps(d) and "GH0101" not in json.dumps(d)
    monkeypatch.setattr(P, "DESCRIBE_DETAIL", False)
    assert "magnitude" not in P.describe(rec) and "ends" not in P.describe(rec)


# -- the silver set builder ---------------------------------------------------------------------------------
_bs_spec = importlib.util.spec_from_file_location("build_silver", TEMPLATE / "build_silver.py")
BS = importlib.util.module_from_spec(_bs_spec)
_bs_spec.loader.exec_module(BS)


@responses.activate
def test_gdacs_refs_reads_the_usgs_event_and_glide_number():
    responses.get(
        f"{BS.GDACS_API}/geteventdata",
        json={
            "properties": {
                "sourceid": "us7000kufc",
                "glide": "EQ-2023-000166-MAR",
                "alertlevel": "Red",
                "source": "NEIC",
            }
        },
    )
    assert BS.gdacs_refs("EQ", 1390643) == {
        "sourceid": "us7000kufc", "glide": "EQ-2023-000166-MAR", "alertlevel": "Red", "source": "NEIC",
    }  # fmt: skip
    assert "eventid=1390643" in responses.calls[0].request.url


def test_silver_from_events_uses_source_references_and_drops_shared_glide_numbers():
    events = [
        {"eventid": 1, "eventtype": "EQ", "name": "Earthquake in Morocco", "fromdate": "2023-09-08T22:11:02",
         "countries": ["MAR"], "glide": "EQ-2023-000166-MAR", "sourceid": "us7000kufc"},
        {"eventid": 2, "eventtype": "EQ", "name": "Earthquake in Morocco", "fromdate": "2023-09-20T00:00:00",
         "countries": ["MAR"], "glide": "EQ-2023-000170-MAR", "sourceid": "us7000zzzz"},
        {"eventid": 3, "eventtype": "TC", "name": "Saola", "fromdate": "2023-08-23T18:00:00",
         "countries": ["CHN"], "glide": "TC-2023-000164-CHN", "sourceid": ""},
        {"eventid": 4, "eventtype": "TC", "name": "Haikui", "fromdate": "2023-08-28T12:00:00",
         "countries": ["CHN", "PHL"], "glide": "TC-2023-000164-PHL", "sourceid": ""},
    ]  # fmt: skip
    found = {
        "gdacs:1": ["gdacs-event-1-10"], "usgs:us7000kufc": ["usgs-event-us7000kufc"],
        "glide:EQ-2023-000166": ["glide-event-EQ-2023-000166-MAR"],
        "gdacs:2": ["gdacs-event-2-20"], "glide:EQ-2023-000170": ["glide-event-EQ-2023-000170-MAR"],
        "gdacs:3": ["gdacs-event-3-1"], "gdacs:4": ["gdacs-event-4-1"],
        "glide:TC-2023-000164": ["glide-event-TC-2023-000164-CHN", "glide-event-TC-2023-000164-PHL"],
    }  # fmt: skip
    silver = BS.silver_from_events(events, found)
    by_id = {d["id"]: d for d in silver["disasters"]}
    assert by_id["gdacs-EQ-1"]["members"] == [
        "gdacs-event-1-10",
        "usgs-event-us7000kufc",
        "glide-event-EQ-2023-000166-MAR",
    ]
    assert by_id["gdacs-EQ-2"]["members"] == ["gdacs-event-2-20", "glide-event-EQ-2023-000170-MAR"]
    # one GLIDE number cited for two storms (with different country suffixes): left out
    assert "gdacs-TC-3" not in by_id and "gdacs-TC-4" not in by_id
    assert by_id["gdacs-EQ-1"]["window"] == by_id["gdacs-EQ-2"]["window"]  # same country, 12 days apart
    assert BS.glide_number("TC-2023-000164-CHN") == ("TC", "2023-000164")
    label, windows, _ = P.gold_labels(silver)
    assert label["glide-event-EQ-2023-000170-MAR"] == "gdacs-EQ-2"


def test_silver_file_is_well_formed_when_present():
    path = TEMPLATE / "silver_disasters.yml"
    if not path.exists():
        pytest.skip("silver_disasters.yml not built")
    silver = yaml.safe_load(path.read_text())
    ids = [m for d in silver["disasters"] for m in d["members"]]
    assert len(ids) == len(set(ids)) and all(len(d["members"]) >= 2 for d in silver["disasters"])
    assert all(P.collection_of(i).endswith("-events") for i in ids)


def test_decision_request_swap_reverses_the_records_only():
    a, b = (
        _rec("usgs-event-1", title="M 7.7 - Mandalay"),
        _rec("emdat-event-1", "emdat", title="Ground movement"),
    )
    plain, swapped = P.decision_request("m", a, b), P.decision_request("m", a, b, swap=True)
    assert plain["state"]["record_a"] == swapped["state"]["record_b"]
    assert plain["state"]["record_b"] == swapped["state"]["record_a"]
    assert plain["state"]["days_apart"] == swapped["state"]["days_apart"]


def test_same_source_links_join_one_disasters_country_records():
    ids = [
        "emdat-event-2023-0095-MOZ", "emdat-event-2023-0095-MWI", "emdat-event-2023-0095-MDG",
        "glide-event-TC-2023-000023-MDG", "idmc-idu-event-15605", "idmc-gidd-event-15605",
        "gdacs-event-1000961-1", "gdacs-event-1000961-9", "emdat-event-2023-0078-MOZ", "2023036S12117",
    ]  # fmt: skip
    assert P.source_key("emdat-event-2023-0095-MOZ") == "emdat:2023-0095"
    assert P.source_key("glide-event-TC-2023-000023-MDG") == "glide:TC-2023-000023"
    assert P.source_key("2023036S12117") is None
    links = P.same_source_links(ids)
    events = P.assemble_events(ids, [a for a, _ in links], [b for _, b in links], [1.0] * len(links), 0.5)
    assert len({events[i] for i in ids if i.startswith("emdat-event-2023-0095")}) == 1
    assert events["emdat-event-2023-0078-MOZ"] != events["emdat-event-2023-0095-MOZ"]
    assert events["idmc-idu-event-15605"] == events["idmc-gidd-event-15605"]
    assert events["gdacs-event-1000961-1"] == events["gdacs-event-1000961-9"]
    assert len(links) == 2 + 1 + 1


def test_hazard_filters():
    storm = _rec("s", "emdat", codes=("MH0301", "ST"))
    flood = _rec("f", "gdacs", codes=("MH0600", "FL"))
    quake = _rec("q", "usgs", codes=("GH0101", "EQ"))
    blank = _rec("b", "glide", codes=())
    assert P.passes_hazard_filter(storm, flood, "related") and not P.passes_hazard_filter(
        storm, flood, "same"
    )
    assert P.passes_hazard_filter(quake, flood, "any")
    assert not P.passes_hazard_filter(quake, flood, "related") and not P.passes_hazard_filter(
        quake, flood, "same"
    )
    assert all(P.passes_hazard_filter(quake, blank, f) for f in P.HAZARD_FILTERS)


def test_upstream_hazard_filters_follow_the_undrr_profiles():
    quake = _rec("q", "usgs", codes=("GH0101", "EQ"))
    tsunami = _rec("t", "emdat", codes=("MH0705", "TS"))
    flood = _rec("f", "gdacs", codes=("MH0600", "FL"))
    cyclone = _rec("c", "ibtracs", codes=("MH0309", "TC"))
    wildfire = _rec("w", "emdat", codes=("EN0205", "WF"))
    assert P.passes_hazard_filter(quake, tsunami, "upstream")  # the earthquake profile: triggers tsunami
    assert P.passes_hazard_filter(cyclone, flood, "upstream")  # tropical cyclone triggers flooding
    assert not P.passes_hazard_filter(quake, flood, "upstream")  # no direct link...
    assert P.passes_hazard_filter(
        quake, flood, "upstream, 2 steps"
    )  # ...but quake -> tsunami -> coastal flooding
    assert not P.passes_hazard_filter(quake, wildfire, "upstream")
    assert P.upstream_related("MH0601", "MH0604")  # one chapeau family (MH0600 Flooding)


def test_small_quakes_are_only_compared_with_quakes():
    small = _rec("usgs-s", "usgs", magnitude=4.6)
    big = _rec("usgs-b", "usgs", magnitude=6.1)
    flagged = P.Record(**{**small.__dict__, "id": "usgs-t", "tsunami": True})
    flood = _rec("idmc-f", "idmc-idu", codes=("MH0600", "FL"))
    register = _rec("emdat-q", "emdat")  # an earthquake register record, no magnitude
    assert not P.passes_hazard_filter(small, flood, "any, quakes M5+")
    assert P.passes_hazard_filter(big, flood, "any, quakes M5+")
    assert P.passes_hazard_filter(flagged, flood, "any, quakes M5+")
    assert P.passes_hazard_filter(small, register, "any, quakes M5+")  # quake to quake: always compared
    assert P.passes_hazard_filter(register, flood, "any, quakes M5+")  # no magnitude: never blocked


def test_lean_requests_carry_the_guidance_once(monkeypatch):
    monkeypatch.setattr(P, "LEAN_PROMPTS", True)
    a = _rec("usgs-1", title="M 7.7 - Mandalay")
    cands = [_rec(f"gdacs-{i}", "gdacs", title=f"Earthquake {i}") for i in range(12)]
    lean = P.listwise_request("m", a, cands)
    assert json.dumps(lean).count(P.LISTWISE_NOTE) == 1 and set(lean["questions"]) == {
        f"same_candidate_{i + 1}" for i in range(12)
    }
    assert set(P.decision_request("m", a, cands[0])["questions"]) == {"same_event"}
    monkeypatch.setattr(P, "LEAN_PROMPTS", False)
    full = P.listwise_request("m", a, cands)
    assert len(json.dumps(lean)) < len(json.dumps(full)) / 2
    assert "relation" in P.decision_request("m", a, cands[0])["questions"]
