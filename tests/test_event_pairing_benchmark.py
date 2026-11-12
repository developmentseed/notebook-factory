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
    assert P.block(a, _rec("b", country="THA"), 45) == "country"
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


def test_decision_request_carries_words_and_measured_facts_but_no_ids_or_codes():
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


# -- the template files -------------------------------------------------------------------------------------------
def test_notebook_parameters_match_template_yml():
    nb = nbformat.read(TEMPLATE / "notebook.ipynb", as_version=4)
    meta = yaml.safe_load((TEMPLATE / "template.yml").read_text())
    (cell,) = [c for c in nb.cells if "parameters" in c.metadata.get("tags", [])]
    names = {t.targets[0].id for t in ast.parse(cell.source).body if isinstance(t, ast.Assign)}
    assert names - set(RESERVED_PARAMETERS) == set(meta["parameters"]["properties"])
    assert meta["secrets"] == ["OPENROUTER_API_KEY"] and meta["requires_stac_item"] is True
