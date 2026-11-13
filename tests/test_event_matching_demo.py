"""The event-matching demo template: candidate rules, exact links, the Jev call, the fact sheet and the template files.

Everything is offline: the two network calls (the Montandon STAC API and OpenRouter's Decisions API) are mocked.
"""

import ast
import copy
import importlib.util
import json
import re
import sys
from pathlib import Path

import nbformat
import pytest
import responses
import yaml

from factory.notebooks.pipeline.inputs import RESERVED_PARAMETERS

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "notebook_templates" / "event-matching-demo"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "montandon" / "myanmar-eq-2025-window.json"

_spec = importlib.util.spec_from_file_location("event_matching_demo", TEMPLATE / "matching.py")
M = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = M
_spec.loader.exec_module(M)

API = "https://stac.example.org/stac"
ITEMS = {f["id"]: f for f in json.loads(FIXTURE.read_text())["features"]}
MAIN_SHOCK = "usgs-event-us7000pn9s"  # M 7.7, 2025-03-28
SMALL_QUAKE = "usgs-event-us7000pnwl"  # M 4.1, 2025-03-30, no tsunami


def _item(item_id, **props):
    """A copy of a fixture item with some properties replaced."""
    item = copy.deepcopy(ITEMS[item_id])
    item["properties"].update(props)
    return item


def _clone(item_id, new_id, collection=None, **props):
    item = _item(item_id, **props)
    item["id"] = new_id
    if collection:
        item["collection"] = collection
    return item


def _mock_search(features):
    """Every POST /search answers with `features`; returns the list of request bodies."""
    responses.reset()
    bodies = []

    def answer(request):
        bodies.append(json.loads(request.body))
        return 200, {}, json.dumps({"features": features})

    responses.add_callback(responses.POST, f"{API}/search", callback=answer, content_type="application/json")
    return bodies


def _rec(item_id):
    return M.record_from_item(ITEMS[item_id])


# -- records ---------------------------------------------------------------------------------------------
def test_record_from_item_reads_source_place_time_and_magnitude():
    usgs = _rec(MAIN_SHOCK)
    assert (usgs.source, usgs.countries, usgs.magnitude, usgs.coarse) == ("usgs", ("MMR",), 7.7, False)
    assert usgs.start.isoformat().startswith("2025-03-28T06:20") and usgs.glide == "EQ"
    emdat = _rec("emdat-event-2025-0223-MMR")
    assert emdat.source == "emdat" and emdat.coarse  # a register: day precision, country-level place
    pdc = _rec("pdc-event-670db46e-234a-4bba-8123-1124f6b6e312-393198-1769565197008")
    assert pdc.magnitude == 7.7  # read from the title
    assert _rec(SMALL_QUAKE).tsunami is False


def test_record_keeps_the_related_links_by_role():
    rec = _rec("emdat-event-2025-0223-MMR")
    roles = {(role, collection) for role, collection, _ in rec.links}
    assert ("hazard", "emdat-hazards") in roles and ("impact", "emdat-impacts") in roles
    cems = _rec("cems-event-EMSR798")
    assert ("event", "gdacs-events", "1474477-1630564") in cems.links


# -- exact links -------------------------------------------------------------------------------------------
def test_event_keys_name_the_shared_register_numbers():
    assert M.event_key("emdat-events", "emdat-event-2023-0600-MAR") == "emdat:2023-0600"
    assert M.event_key("glide-events", "glide-event-EQ-2025-000043-MMR") == "glide:EQ-2025-000043"
    assert M.event_key("idmc-idu-events", "idmc-idu-event-18260") == "idmc:18260"
    assert M.event_key("idmc-gidd-events", "idmc-gidd-event-18260") == "idmc:18260"
    assert M.event_key("gdacs-events", "gdacs-event-1474477-1630564") == "gdacs:1474477"
    assert M.event_key("gdacs-events", "1474477-1630564") == "gdacs:1474477"  # as a link target
    assert M.event_key("usgs-events", "usgs-event-us7000pn9s") is None


def test_exact_links_join_records_that_share_a_register_number():
    seed = _rec("emdat-event-2025-0223-MMR")
    same = M.record_from_item(_clone("emdat-event-2025-0223-MMR", "emdat-event-2025-0223-THA"))
    other = M.record_from_item(_clone("emdat-event-2025-0223-MMR", "emdat-event-2025-0999-MMR"))
    links = M.exact_links(seed, [seed, same, other, _rec(MAIN_SHOCK)])
    assert list(links) == [same.id]
    assert "EM-DAT" in links[same.id] and "2025-0223" in links[same.id]


def test_exact_links_follow_a_related_event_link_to_another_source():
    seed = _rec("cems-event-EMSR798")  # links to GDACS event 1474477
    gdacs = _rec("gdacs-event-1474477-1630564")
    unrelated = _rec("gdacs-event-1474489-1630585")
    links = M.exact_links(seed, [gdacs, unrelated])
    assert list(links) == [gdacs.id] and "GDACS" in links[gdacs.id]


def test_exact_links_pair_the_two_idmc_collections():
    idu = _rec("idmc-idu-event-30892")
    gidd = M.record_from_item(_clone("idmc-idu-event-30892", "idmc-gidd-event-30892", "idmc-gidd-events"))
    assert list(M.exact_links(idu, [gidd])) == [gidd.id]


# -- the rules a user can change ---------------------------------------------------------------------------
def test_parse_windows_reads_one_hazard_per_line():
    text = "EQ 7 300\n tc  21 2500   # cyclones\n\n# a comment line\nFL 30 500"
    assert M.parse_windows(text) == {"EQ": (7, 300), "TC": (21, 2500), "FL": (30, 500)}


@pytest.mark.parametrize(
    "bad", ["EQ 7", "EQ seven 300", "EARTHQUAKE 7 300", "EQ -1 300", "EQ 7 0", "EQ 7 300 9"]
)
def test_parse_windows_names_the_bad_line(bad):
    with pytest.raises(ValueError, match="line 2"):
        M.parse_windows(f"TC 21 2500\n{bad}")


def test_the_default_rules_are_the_benchmark_values_everywhere_they_are_written():
    assert M.parse_windows(M.DEFAULT_WINDOWS_TEXT) == M.HAZARD_WINDOWS
    assert M.HAZARD_WINDOWS["EQ"] == (7, 300) and M.HAZARD_WINDOWS["TC"] == (21, 2500)
    props = _meta()["parameters"]["properties"]
    assert M.parse_windows(props["hazard_windows"]["default"]) == M.HAZARD_WINDOWS
    assert M.Rules.from_params(**{k: props[k]["default"] for k in M.RULE_PARAMETERS}) == M.Rules()


def test_rules_from_params_rejects_values_that_would_search_nothing():
    with pytest.raises(ValueError, match="radius"):
        M.Rules.from_params("EQ 7 300", 14, 500, 0, 5.0)
    with pytest.raises(ValueError, match="days"):
        M.Rules.from_params("EQ 7 300", 0, 500, 1500, 5.0)


def test_a_hazard_missing_from_the_table_uses_the_default_window():
    rules = M.Rules.from_params("TC 21 2500", 10, 400, 1500, 5.0)
    assert rules.window(_rec(MAIN_SHOCK)) == (10, 400)
    assert M.Rules().window(_rec(MAIN_SHOCK)) == (7, 300)


# -- candidates --------------------------------------------------------------------------------------------
@responses.activate
def test_a_shorter_window_and_a_smaller_radius_find_fewer_candidates():
    seed = _rec(MAIN_SHOCK)
    two_days_later = _clone("gdacs-event-1474489-1630585", "gdacs-event-7-1", start_datetime="2025-03-30T06:20:00+00:00",
                            end_datetime="2025-03-30T06:20:00+00:00", datetime="2025-03-30T06:20:00Z")  # fmt: skip
    _mock_search([two_days_later])
    assert [r.id for r in M.find_candidates(API, "tok", seed, max_items=10)[0]] == ["gdacs-event-7-1"]
    tight = M.Rules.from_params("EQ 1 300", 14, 500, 1500, 5.0)
    bodies = _mock_search([two_days_later])
    assert M.find_candidates(API, "tok", seed, max_items=10, rules=tight)[0] == []
    assert bodies[0]["datetime"].startswith("2025-03-27T")


@responses.activate
def test_a_smaller_radius_drops_a_cross_border_record_that_the_default_keeps():
    seed = _rec(MAIN_SHOCK)
    near = _clone("gdacs-event-1474489-1630585", "gdacs-event-8-1")
    near["properties"]["monty:country_codes"] = ["THA"]
    near["geometry"] = {"type": "Point", "coordinates": [97.5, 21.0]}  # about 200 km away
    _mock_search([near])
    assert len(M.find_candidates(API, "tok", seed, max_items=10)[0]) == 1
    small = M.Rules.from_params("EQ 7 100", 14, 500, 100, 5.0)
    assert M.find_candidates(API, "tok", seed, max_items=10, rules=small)[0] == []


@responses.activate
def test_the_quake_magnitude_cut_off_is_a_parameter():
    flood = _clone(
        "gdacs-event-1474489-1630585", "gdacs-event-9-1", **{"monty:hazard_codes": ["MH0600", "FL"]}
    )
    small = _rec(SMALL_QUAKE)  # M 4.1
    _mock_search([flood])
    assert M.find_candidates(API, "tok", small, max_items=10)[0] == []
    lenient = M.Rules.from_params(M.DEFAULT_WINDOWS_TEXT, 14, 500, 1500, 4.0)
    assert [r.id for r in M.find_candidates(API, "tok", small, max_items=10, rules=lenient)[0]] == [
        "gdacs-event-9-1"
    ]


@responses.activate
def test_candidates_search_by_country_and_by_radius_over_the_event_collections():
    seed = _rec(MAIN_SHOCK)
    bodies = _mock_search([ITEMS["cems-event-EMSR798"]])
    M.find_candidates(API, "tok", seed, max_items=10)
    country, radius = bodies
    assert set(country["collections"]) == set(M.EVENT_COLLECTIONS) and "emdat-events" in M.EVENT_COLLECTIONS
    assert country["filter"] == {
        "op": "a_overlaps",
        "args": [{"property": "monty:country_codes"}, ["MMR"]],
    }
    assert country["datetime"].startswith("2025-03-21T06:20") and "/2025-04-04T06:20" in country["datetime"]
    west, south, east, north = radius["bbox"]
    assert west < seed.point[0] < east and south < seed.point[1] < north
    assert responses.calls[0].request.headers["Authorization"] == "Bearer tok"


@responses.activate
def test_candidates_keep_other_sources_inside_the_window_and_drop_the_rest():
    seed = _rec(MAIN_SHOCK)
    late = _clone("gdacs-event-1474489-1630585", "gdacs-event-1-1", datetime="2025-06-01T00:00:00Z",
                  start_datetime="2025-06-01T00:00:00+00:00", end_datetime="2025-06-01T00:00:00+00:00")  # fmt: skip
    far = _clone("gdacs-event-1474489-1630585", "gdacs-event-2-1")
    far["properties"]["monty:country_codes"] = ["PER"]
    far["geometry"] = {"type": "Point", "coordinates": [-75.0, -10.0]}
    _mock_search([*ITEMS.values(), late, far])
    found, total = M.find_candidates(API, "tok", seed, max_items=100)
    ids = {r.id for r in found}
    assert seed.id not in ids
    assert not {i for i in ids if i.startswith("usgs-")}, "the seed's own source is not a candidate"
    assert {"emdat-event-2025-0223-MMR", "cems-event-EMSR798", "ifrcevent-event-7464"} <= ids
    assert "gdacs-event-1-1" not in ids and "gdacs-event-2-1" not in ids
    assert total == len(found)


@responses.activate
def test_candidates_are_capped_and_the_most_plausible_come_first():
    seed = _rec(MAIN_SHOCK)
    _mock_search(list(ITEMS.values()))
    found, total = M.find_candidates(API, "tok", seed, max_items=5)
    assert len(found) == 5 and total > 5
    scores = [M.heuristic_score(*M.pair_anchor(seed, r)) for r in found]
    assert scores == sorted(scores, reverse=True)


@responses.activate
def test_candidates_include_a_cross_border_record_inside_the_radius_but_not_beyond_it():
    seed = _rec(MAIN_SHOCK)
    near = _clone("gdacs-event-1474489-1630585", "gdacs-event-3-1")  # same quake, filed under Thailand
    near["properties"]["monty:country_codes"] = ["THA"]
    near["geometry"] = {"type": "Point", "coordinates": [97.5, 21.0]}
    beyond = _clone("gdacs-event-1474489-1630585", "gdacs-event-4-1")
    beyond["properties"]["monty:country_codes"] = ["IND"]
    beyond["geometry"] = {"type": "Point", "coordinates": [80.0, 13.0]}
    _mock_search([near, beyond])
    found, _ = M.find_candidates(API, "tok", seed, max_items=10)
    assert [r.id for r in found] == ["gdacs-event-3-1"]


@responses.activate
def test_a_small_quake_is_only_compared_with_quakes():
    flood = _clone(
        "gdacs-event-1474489-1630585", "gdacs-event-5-1", **{"monty:hazard_codes": ["MH0600", "FL"]}
    )
    quake = _item("gdacs-event-1474489-1630585")
    small = _rec(SMALL_QUAKE)
    _mock_search([flood, quake])
    found, _ = M.find_candidates(API, "tok", small, max_items=10)
    assert [r.id for r in found] == [quake["id"]]
    big = _rec(MAIN_SHOCK)  # the same flood is fine for a M 7.7 (it can trigger floods and landslides)
    found, _ = M.find_candidates(API, "tok", big, max_items=10)
    assert {r.id for r in found} == {"gdacs-event-5-1", quake["id"]}


@responses.activate
def test_a_small_quake_with_a_tsunami_flag_may_still_match_other_hazards():
    flood = _clone(
        "gdacs-event-1474489-1630585", "gdacs-event-6-1", **{"monty:hazard_codes": ["MH0600", "FL"]}
    )
    small = M.record_from_item(_item(SMALL_QUAKE, **{"eq:tsunami": True}))
    _mock_search([flood])
    found, _ = M.find_candidates(API, "tok", small, max_items=10)
    assert [r.id for r in found] == ["gdacs-event-6-1"]


@responses.activate
def test_candidates_keep_siblings_of_the_seeds_own_source_only_when_exactly_linked():
    seed = _rec("emdat-event-2025-0223-MMR")
    sibling = _clone("emdat-event-2025-0223-MMR", "emdat-event-2025-0223-THA")
    stranger = _clone("emdat-event-2025-0223-MMR", "emdat-event-2025-0777-MMR")
    _mock_search([sibling, stranger])
    found, _ = M.find_candidates(API, "tok", seed, max_items=10)
    assert [r.id for r in found] == ["emdat-event-2025-0223-THA"]


@responses.activate
def test_search_follows_next_links():
    first = {
        "features": [ITEMS[MAIN_SHOCK]],
        "links": [{"rel": "next", "method": "POST", "body": {"token": "t2"}}],
    }
    second = {"features": [ITEMS["emdat-event-2025-0223-MMR"]], "links": []}
    responses.add(responses.POST, f"{API}/search", json=first)
    responses.add(responses.POST, f"{API}/search", json=second)
    got = M._search(API, "tok", {"collections": ["usgs-events"], "limit": 1})
    assert [f["id"] for f in got] == [MAIN_SHOCK, "emdat-event-2025-0223-MMR"]
    assert json.loads(responses.calls[1].request.body)["token"] == "t2"


def test_a_seed_without_a_date_cannot_be_searched():
    seed = M.record_from_item({**_item(MAIN_SHOCK), "properties": {"title": "x"}})
    with pytest.raises(ValueError, match="date"):
        M.find_candidates(API, "tok", seed, max_items=5)


# -- Jev ---------------------------------------------------------------------------------------------------
DECISIONS = "https://openrouter.ai/api/alpha/decisions"


def _jev_answer(request):
    """P(same) 0.9 for the candidates in Myanmar, 0.1 for the rest; the cost of a call is 0.001."""
    payload = json.loads(request.body)
    cands = payload["state"]["candidates"]
    answers = {
        f"same_{key}": {"type": "noul", "noul": 0.9 if "MMR" in c["countries"] else 0.1}
        for key, c in cands.items()
    }
    answers["any_match"] = {"type": "noul", "noul": 0.9}
    return 200, {}, json.dumps({"answers": answers, "model": payload["model"], "usage": {"cost": 0.001}})


@responses.activate
def test_ask_jev_returns_p_same_for_every_candidate():
    seed = _rec(MAIN_SHOCK)
    cands = [_rec("emdat-event-2025-0223-MMR"), _rec("glide-event-EQ-2025-000043-MMR")]
    cands.append(M.record_from_item(_clone("glide-event-EQ-2025-000043-MMR", "glide-event-EQ-2025-000099-PER",
                                          title="EQ in Lima", **{"monty:country_codes": ["PER"]})))  # fmt: skip
    responses.add_callback(responses.POST, DECISIONS, callback=_jev_answer, content_type="application/json")
    answer = M.ask_jev(seed, cands, "key", "some/model")
    assert answer.scores == {cands[0].id: 0.9, cands[1].id: 0.9, cands[2].id: 0.1}
    assert answer.cost == pytest.approx(0.002) and answer.errors == []
    assert [c.request.headers["Authorization"] for c in responses.calls] == [
        "Bearer key"
    ] * 2  # one call per source
    first = json.loads(responses.calls[0].request.body)
    assert first["state"]["anchor"]["title"].startswith("M 7.7") and first["model"] == "some/model"
    assert set(first["questions"]) >= {"same_candidate_1", "best", "any_match"}


@responses.activate
def test_ask_jev_splits_a_source_into_chunks_of_25():
    seed = _rec(MAIN_SHOCK)
    base = _item("gdacs-event-1474489-1630585")
    cands = []
    for i in range(30):
        item = copy.deepcopy(base)
        item["id"] = f"gdacs-event-{100 + i}-1"
        cands.append(M.record_from_item(item))
    responses.add_callback(responses.POST, DECISIONS, callback=_jev_answer, content_type="application/json")
    answer = M.ask_jev(seed, cands, "key", "m")
    sizes = sorted(len(json.loads(c.request.body)["state"]["candidates"]) for c in responses.calls)
    assert sizes == [5, 25] and len(answer.scores) == 30


@responses.activate
def test_ask_jev_without_a_key_makes_no_call():
    answer = M.ask_jev(_rec(MAIN_SHOCK), [_rec("emdat-event-2025-0223-MMR")], "", "m")
    assert answer.scores == {} and answer.cost == 0 and len(responses.calls) == 0
    assert M.ask_jev(_rec(MAIN_SHOCK), [], "key", "m").scores == {}


@responses.activate
def test_ask_jev_reports_a_failed_call_and_keeps_the_rest(monkeypatch):
    monkeypatch.setattr(M.time, "sleep", lambda s: None)
    seed = _rec(MAIN_SHOCK)
    cands = [_rec("emdat-event-2025-0223-MMR"), _rec("glide-event-EQ-2025-000043-MMR")]

    def answer(request):
        payload = json.loads(request.body)
        if "EM-DAT" in json.dumps(payload["state"]):
            return 500, {}, "{}"
        return _jev_answer(request)

    responses.add_callback(responses.POST, DECISIONS, callback=answer, content_type="application/json")
    result = M.ask_jev(seed, cands, "key", "m")
    assert result.scores == {cands[1].id: 0.9} and len(result.errors) == 1
    assert "key" not in result.errors[0]


# -- details -------------------------------------------------------------------------------------------------
def _impact(item_id, type_, category, value, unit="count", estimate="primary"):
    return {"id": item_id, "geometry": None, "properties": {"monty:impact_detail": {
        "type": type_, "category": category, "value": value, "unit": unit, "estimate_type": estimate}}}  # fmt: skip


def _hazard(item_id, value, unit, geometry=None):
    return {"id": item_id, "geometry": geometry, "properties": {"title": item_id, "monty:hazard_detail": {
        "severity_value": value, "severity_unit": unit, "estimate_type": "primary"}}}  # fmt: skip


SQUARE = {"type": "Polygon", "coordinates": [[[95, 21], [96, 21], [96, 22], [95, 22], [95, 21]]]}


def _linked(rec, *links):
    return M.Record(**{**rec.__dict__, "links": tuple(links)})


def _serve(stock):
    """A /search that answers with the stock items of the requested collection and ids."""
    wanted = []

    def answer(request):
        body = json.loads(request.body)
        wanted.append((body["collections"], body["ids"]))
        (collection,) = body["collections"]
        return (
            200,
            {},
            json.dumps({"features": [f for f in stock.get(collection, []) if f["id"] in body["ids"]]}),
        )

    responses.add_callback(responses.POST, f"{API}/search", callback=answer, content_type="application/json")
    return wanted


@responses.activate
def test_fetch_details_follows_related_links_and_keeps_only_what_counts():
    emdat = _linked(_rec("emdat-event-2025-0223-MMR"),
                    ("impact", "emdat-impacts", "emdat-impact-2025-0223-MMR-total_deaths"),
                    ("impact", "emdat-impacts", "emdat-impact-2025-0223-MMR-total_dam"),
                    ("hazard", "emdat-hazards", "emdat-hazard-2025-0223-MMR"))  # fmt: skip
    usgs = _linked(_rec(MAIN_SHOCK),
                   ("impact", "usgs-impacts", "usgs-impact-us7000pn9s-fatalities-MMR"),
                   ("impact", "usgs-impacts", "usgs-impact-us7000pn9s-fatality-MMR"),
                   ("impact", "usgs-impacts", "usgs-impact-us7000pn9s-economic-MMR"),
                   ("hazard", "usgs-hazards", "usgs-hazard-us7000pn9s-shakemap"))  # fmt: skip
    stock = {
        "emdat-impacts": [
            _impact("emdat-impact-2025-0223-MMR-total_deaths", "death", "people", 3726),
            _impact("emdat-impact-2025-0223-MMR-total_dam", "cost", "total_affected", 7000000),
        ],
        "usgs-impacts": [
            _impact("usgs-impact-us7000pn9s-fatalities-MMR", "death", "people", 1665, "people", "modelled"),
            _impact(
                "usgs-impact-us7000pn9s-fatality-MMR",
                "potentially_affected",
                "people",
                268302,
                "people",
                "modelled",
            ),
            _impact(
                "usgs-impact-us7000pn9s-economic-MMR", "cost", "buildings", 2496081634, "usd", "modelled"
            ),
        ],
        "usgs-hazards": [_hazard("usgs-hazard-us7000pn9s-shakemap", 7.7, "mww", SQUARE)],
        "emdat-hazards": [
            _hazard("emdat-hazard-2025-0223-MMR", 7.7, "Moment Magnitude", SQUARE)
        ],  # a country outline
    }
    wanted = _serve(stock)
    details = M.fetch_details(API, "tok", [emdat, usgs])
    by = {(r["source"], r["field"]): r for r in details.rows}
    assert by[("emdat", "deaths")]["value"] == 3726 and by[("emdat", "deaths")]["estimate_type"] == "primary"
    assert (
        by[("usgs", "deaths")]["estimate_type"] == "modelled" and by[("usgs", "deaths")]["unit"] == "people"
    )
    assert by[("usgs", "loss")]["unit"] == "usd" and by[("emdat", "loss")]["unit"] == "count"
    assert by[("usgs", "severity")]["unit"] == "mww"
    assert ("usgs", "affected") not in by, "potentially affected is an exposure, not an impact"
    assert [f["properties"]["source"] for f in details.footprints] == ["usgs"]
    assert {c for cs, _ in wanted for c in cs} == {
        "emdat-hazards",
        "emdat-impacts",
        "usgs-hazards",
        "usgs-impacts",
    }


@responses.activate
def test_fetch_details_skips_age_bands_and_exposed_capital_and_unknown_severity():
    stock = {
        "pdc-impacts": [
            _impact("pdc-impact-a", "affected_total", "people", 2360000, None),
            _impact("pdc-impact-b", "affected_total", "children_0_4", 198948, None),
            _impact("pdc-impact-c", "affected_total", "elderly", 185024, None),
            _impact("pdc-impact-d", "cost", "global_currency", 22975640500, None),
        ],
        "pdc-hazards": [_hazard("pdc-hazard-a", -1, "glide")],
    }
    _serve(stock)
    pdc = _linked(_rec("pdc-event-670db46e-234a-4bba-8123-1124f6b6e312-393198-1769565197008"),
                  *(("impact", "pdc-impacts", f"pdc-impact-{x}") for x in "abcd"),
                  ("hazard", "pdc-hazards", "pdc-hazard-a"))  # fmt: skip
    details = M.fetch_details(API, "tok", [pdc])
    assert [(r["field"], r["value"]) for r in details.rows] == [("affected", 2360000)]
    assert details.footprints == []


@responses.activate
def test_fetch_details_batches_ids_per_collection():
    ids = tuple(("impact", "pdc-impacts", f"pdc-impact-{i}") for i in range(120))
    rec = M.Record(**{**_rec("emdat-event-2025-0223-MMR").__dict__, "links": ids})
    bodies = _mock_search([])
    M.fetch_details(API, "tok", [rec])
    assert [len(b["ids"]) for b in bodies] == [50, 50, 20]
    assert all(b["collections"] == ["pdc-impacts"] for b in bodies)


# -- fact sheet -------------------------------------------------------------------------------------------------
def _row(source, field, value, unit="count", estimate="primary"):
    return {
        "source": source,
        "field": field,
        "value": value,
        "unit": unit,
        "estimate_type": estimate,
        "event": source,
    }


MOROCCO = [
    _row("usgs", "deaths", 1665, "people", "modelled"),
    _row("emdat", "deaths", 2946),
    _row("ifrcevent", "deaths", 2946, None),
    _row("emdat", "affected", 845674),
    _row("pdc", "affected", 2360000, None),
    _row("ifrcevent", "affected", 6600000, None),
    _row("idmc-idu", "displaced", 145695),
    _row("ifrcevent", "displaced", 500000, None),
    _row("emdat", "injured", 5674),
    _row("ifrcevent", "injured", 5700, None),
]


def test_fact_sheet_flags_the_rows_where_sources_disagree():
    records = [_rec(MAIN_SHOCK), _rec("emdat-event-2025-0223-MMR"), _rec("ifrcevent-event-7464")]
    table = M.fact_sheet(records, MOROCCO)
    assert list(table.loc["Deaths", ["USGS", "EM-DAT", "IFRC"]]) == [1665, 2946, 2946]
    assert bool(table.loc["Deaths", "Sources disagree"]) and table.loc["Deaths", "Range"] == "1,665 – 2,946"
    assert isinstance(table.loc["Deaths", "USGS"], int), (
        "whole numbers stay whole (the CSV shows 1665, not 1665.0)"
    )
    assert bool(table.loc["People affected", "Sources disagree"])
    assert bool(table.loc["People displaced", "Sources disagree"])
    assert not bool(table.loc["Injured", "Sources disagree"])  # 5,674 against 5,700
    assert ("Deaths", "USGS") in table.attrs["modelled"] and ("Deaths", "EM-DAT") not in table.attrs[
        "modelled"
    ]
    assert list(table.index)[:3] == ["Starts", "Ends", "Countries"]
    assert table.loc["Countries", "EM-DAT"] == "MMR" and table.loc["Starts", "EM-DAT"] == "2025-03-28"
    assert "Economic loss" not in table.index  # no source gave one


def test_fact_sheet_has_one_column_per_source_in_a_fixed_order():
    records = [_rec("emdat-event-2025-0223-MMR"), _rec(MAIN_SHOCK), _rec("ifrcevent-event-7464")]
    assert list(M.fact_sheet(records, MOROCCO).columns) == ["USGS", "EM-DAT", "IFRC", "IDMC (IDU)", "PDC", "Range", "Sources disagree"]  # fmt: skip


def test_fact_sheet_compares_values_in_one_unit_only():
    rows = [_row("usgs", "loss", 2.5e9, "usd", "modelled"), _row("emdat", "loss", 7e6, "count")]
    table = M.fact_sheet([_rec(MAIN_SHOCK), _rec("emdat-event-2025-0223-MMR")], rows)
    assert not bool(table.loc["Economic loss", "Sources disagree"]), (
        "USD and EM-DAT's unit are not comparable"
    )
    assert table.attrs["units"][("Economic loss", "USGS")] == "usd"


def test_fact_sheet_sums_copernicus_areas_and_takes_the_largest_value_elsewhere():
    rows = [_row("cems", "affected", 17000, "people"), _row("cems", "affected", 3300, "people"),
            _row("emdat", "affected", 840000), _row("emdat", "affected", 845674)]  # fmt: skip
    table = M.fact_sheet([_rec("cems-event-EMSR798"), _rec("emdat-event-2025-0223-MMR")], rows)
    assert table.loc["People affected", "CEMS"] == 20300 and table.loc["People affected", "EM-DAT"] == 845674


def test_fact_sheet_magnitude_prefers_the_record_then_the_hazard_item():
    rows = [_row("emdat", "severity", 7.7, "Moment Magnitude"), _row("usgs", "severity", 7.6, "mww")]
    table = M.fact_sheet([_rec(MAIN_SHOCK), _rec("emdat-event-2025-0223-MMR")], rows)
    assert table.loc["Magnitude / severity", "USGS"] == 7.7  # the catalogue's own magnitude
    assert table.loc["Magnitude / severity", "EM-DAT"] == 7.7
    assert table.attrs["units"][("Magnitude / severity", "EM-DAT")] == "Moment Magnitude"
    assert not bool(table.loc["Magnitude / severity", "Sources disagree"])
    assert table.loc["Magnitude / severity", "Range"] == "7.7"
    rows = [_row("emdat", "severity", 7.9, "Moment Magnitude")]
    table = M.fact_sheet([_rec(MAIN_SHOCK), _rec("emdat-event-2025-0223-MMR")], rows)
    assert (
        table.loc["Magnitude / severity", "Range"] == "7.7 – 7.9"
    )  # one decimal, and no "disagree" flag for a log scale


def test_fact_sheet_spans_the_dates_of_a_source_s_records():
    records = [_rec(MAIN_SHOCK), _rec("usgs-event-us7000pnqp"), _rec("usgs-event-us6000q4s3")]
    table = M.fact_sheet(records, [])
    assert table.loc["Starts", "USGS"] == "2025-03-28" and table.loc["Ends", "USGS"] == "2025-03-29"
    assert "Deaths" not in table.index


# -- the match table ------------------------------------------------------------------------------------------------
def test_match_table_sorts_by_confidence_and_says_why():
    seed = _rec("cems-event-EMSR798")
    gdacs, emdat, glide = (_rec(i) for i in ("gdacs-event-1474477-1630564", "emdat-event-2025-0223-MMR",
                                            "glide-event-EQ-2025-000043-MMR"))  # fmt: skip
    exact = M.exact_links(seed, [gdacs, emdat, glide])
    table = M.match_table([gdacs, emdat, glide], exact, {emdat.id: 0.97, glide.id: 0.41})
    assert list(table["id"]) == [gdacs.id, emdat.id, glide.id]
    assert list(table["p_same"]) == [1.0, 0.97, 0.41]
    assert table.loc[0, "why"].startswith("same GDACS") and table.loc[1, "why"] == "Jev"
    assert table.loc[1, "source"] == "EM-DAT"


def test_match_table_without_scores_keeps_the_candidates_unscored():
    emdat = _rec("emdat-event-2025-0223-MMR")
    table = M.match_table([emdat], {}, {})
    assert table.loc[0, "p_same"] == 0.0 and table.loc[0, "why"] == "not scored"


# -- the template files -----------------------------------------------------------------------------------------------
def _notebook():
    return nbformat.read(TEMPLATE / "notebook.ipynb", as_version=4)


def _meta():
    return yaml.safe_load((TEMPLATE / "template.yml").read_text())


def _parameters_cell(nb):
    cells = [c for c in nb.cells if c.cell_type == "code" and "parameters" in c.metadata.get("tags", [])]
    assert len(cells) == 1 and nb.cells.index(cells[0]) == min(
        i for i, c in enumerate(nb.cells) if c.cell_type == "code"
    ), "exactly one tagged cell, and it is the first code cell"
    return cells[0]


def test_notebook_user_parameters_match_template_yml():
    defaults = {}
    for node in ast.parse(_parameters_cell(_notebook()).source).body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            defaults[node.targets[0].id] = ast.literal_eval(node.value)
    assert set(RESERVED_PARAMETERS) <= set(defaults), (
        "declare every reserved parameter so it also runs standalone"
    )
    user = {k: v for k, v in defaults.items() if k not in RESERVED_PARAMETERS}
    props = _meta()["parameters"]["properties"]
    assert set(user) == set(props) == {"confidence_threshold", "max_candidates", *M.RULE_PARAMETERS}
    assert {k: props[k]["default"] for k in props} == user
    assert user["confidence_threshold"] == 0.6 and user["max_candidates"] == 60


def test_template_yml_settings():
    meta = _meta()
    assert meta["requires_area"] is False and meta["requires_stac_item"] is True
    assert meta["stac_hints"] == {"profile": "montandon", "roles": ["event"]}
    assert meta["secrets"] == ["OPENROUTER_API_KEY"]
    assert meta["parameters"]["properties"]["confidence_threshold"]["maximum"] == 1


def test_the_notebook_reads_the_api_key_only_from_the_environment():
    nb = _notebook()
    source = "\n".join(c.source for c in nb.cells if c.cell_type == "code")
    assert 'os.environ.get("OPENROUTER_API_KEY"' in source or 'os.environ["OPENROUTER_API_KEY"]' in source
    assert "sk-or-" not in source and "OPENROUTER_API_KEY =" not in source


def test_matching_module_needs_no_django():
    tree = ast.parse((TEMPLATE / "matching.py").read_text())
    imported = {n.names[0].name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import)}
    imported |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert not imported & {"django", "factory"}


def test_display_table_formats_dates_marks_modelled_figures_and_paints_disagreements():
    records = [_rec(MAIN_SHOCK), _rec("emdat-event-2025-0223-MMR"), _rec("ifrcevent-event-7464")]
    html = M.display_table(M.fact_sheet(records, MOROCCO)).to_html()
    assert "28\u00a0Mar\u00a025" in html and "1,665*" in html and "2,946" in html
    assert "Sources disagree" not in html, "the highlight says it; the column stays in the CSV"
    painted = re.search(r"([^{}]*)\{[^{}]*fbd9b8", html).group(1)  # one rule, listing every painted cell
    assert painted.count("#T_") == 8  # deaths x3, people affected x3, people displaced x2; injured agrees


def test_map_view_frames_the_points():
    near = M.map_view([(-8.4, 31.1), (-8.0, 31.3)])
    wide = M.map_view([(-9.0, 30.0), (3.0, 37.0)])
    assert near["zoom"] > wide["zoom"] >= 1.0
    assert wide["longitude"] == -3.0 and wide["latitude"] == 33.5
    assert M.map_view([(10.0, 10.0)])["zoom"] == 8.0  # one point: as close as it goes


def test_fact_sheet_adds_a_registers_records_of_different_countries():
    """EM-DAT and GLIDE file one record per country of a disaster: the totals add up."""
    mmr = _rec("emdat-event-2025-0223-MMR")
    tha = M.record_from_item(
        _clone("emdat-event-2025-0223-MMR", "emdat-event-2025-0223-THA", **{"monty:country_codes": ["THA"]})
    )
    rows = [
        {**_row("emdat", "deaths", 3726), "event": mmr.id},
        {**_row("emdat", "deaths", 50), "event": tha.id},
    ]
    assert M.fact_sheet([mmr, tha], rows).loc["Deaths", "EM-DAT"] == 3776


def test_fact_sheet_takes_the_largest_of_records_in_one_country():
    """USGS files a record per shock, all in one country: the figures are not additive."""
    main, aftershock = _rec(MAIN_SHOCK), _rec("usgs-event-us7000pnqp")
    rows = [
        {**_row("usgs", "deaths", 1665), "event": main.id},
        {**_row("usgs", "deaths", 40), "event": aftershock.id},
    ]
    assert M.fact_sheet([main, aftershock], rows).loc["Deaths", "USGS"] == 1665


def test_fact_sheet_gives_no_magnitude_range_across_unrelated_units():
    rows = [
        _row("usgs", "severity", 240, "km/h"),
        _row("emdat", "severity", 8552.7, "km2"),
        _row("cems", "severity", 165, "kts"),
    ]
    records = [_rec(MAIN_SHOCK), _rec("emdat-event-2025-0223-MMR"), _rec("cems-event-EMSR798")]
    records = [M.Record(**{**r.__dict__, "magnitude": None}) for r in records]
    table = M.fact_sheet(records, rows)
    assert table.loc["Magnitude / severity", "Range"] == ""
    assert table.attrs["units"][("Magnitude / severity", "CEMS")] == "kts"
