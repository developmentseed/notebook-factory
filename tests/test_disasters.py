"""Disaster search: refs, grouping across sources, the disaster page, browse and the API.

The fixtures are real Montandon responses (event collections, same country and
hazard, a week around the event) for the 2025-03-28 Myanmar M7.7 earthquake
and the 2026-10-22 Xizang M7.0 earthquake.
"""

import json
from pathlib import Path

import pytest
from django.core.cache import cache
from django.urls import reverse

from factory.events import disasters as D
from factory.events import refs as R
from factory.events.matching import upsert_item
from factory.events.models import EventLinkOverride, StacItem
from factory.events.stac_client import StacClientError
from factory.notebooks.models import NotebookRun
from factory.notebooks.services import create_run

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "montandon"
MYANMAR_IDS = [
    "glide-event-EQ-2025-000043-MMR",
    "ifrcevent-event-7464",
    "cems-event-EMSR798",
    "emdat-event-2025-0223-MMR",
    "idmc-idu-event-30892",
    "usgs-event-us7000pn9s",
]


def _features(name):
    return json.loads((FIXTURES / name).read_text())["features"]


@pytest.fixture
def myanmar():
    return _features("myanmar-eq-2025-window.json")


@pytest.fixture
def china():
    return _features("china-eq-2026-window.json")


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


class FakeMonty:
    """Answers the few STAC calls disaster resolution makes, from fixture items."""

    def __init__(self, items, fail=False):
        self.items = items
        self.fail = fail
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def _check(self):
        if self.fail:
            raise StacClientError("POST /search -> 503: down")

    def collections(self):
        self._check()
        return [{"id": c} for c in sorted({i["collection"] for i in self.items} | {"usgs-hazards"})]

    def get_item(self, collection, item_id):
        self._check()
        self.calls.append(("get", collection, item_id))
        for i in self.items:
            if i["collection"] == collection and i["id"] == item_id:
                return i
        raise StacClientError(f"GET /collections/{collection}/items/{item_id} -> 404: not found")

    def search(self, collections, *, filter=None, max_items=None, **kwargs):  # noqa: A002
        self._check()
        self.calls.append(("search", tuple(collections), json.dumps(filter, sort_keys=True)))
        out = [i for i in self.items if i["collection"] in collections]
        if filter and filter.get("op") == "=":
            prop, value = filter["args"][0]["property"], filter["args"][1]
            out = [i for i in out if i["properties"].get(prop) == value]
        return iter(out[:max_items] if max_items else out)


@pytest.fixture
def fake_monty(monkeypatch, montandon, myanmar):
    from factory.events.models import StacCatalog

    fake = FakeMonty(myanmar)
    monkeypatch.setattr(StacCatalog, "client", lambda self: fake)
    return fake


# -- refs -----------------------------------------------------------------------
@pytest.mark.parametrize(
    "text, expected",
    [
        ("EQ-2025-000043-MMR", ["glide:EQ-2025-000043-MMR", "glide:EQ-2025-000043"]),
        ("eq-2025-000043", ["glide:EQ-2025-000043"]),
        ("glide:EQ-2025-000043-MMR", ["glide:EQ-2025-000043-MMR", "glide:EQ-2025-000043"]),
        ("EMSR798", ["cems:EMSR798"]),
        ("https://rapidmapping.emergency.copernicus.eu/EMSR798/", ["cems:EMSR798"]),
        ("go:7464", ["ifrcevent:7464"]),
        ("https://go.ifrc.org/emergencies/7464", ["ifrcevent:7464"]),
        ("2025-0223-MMR", ["emdat:2025-0223-MMR"]),
        ("us7000pn9s", ["usgs:us7000pn9s"]),
        ("https://earthquake.usgs.gov/earthquakes/eventpage/us7000pn9s", ["usgs:us7000pn9s"]),
        ("EQ1474477", ["gdacs:1474477"]),
        ("https://www.gdacs.org/report.aspx?eventid=1474477&eventtype=EQ", ["gdacs:1474477"]),
        ("idmc-idu:30892", ["idmc-idu:30892"]),
        (
            "20250328-MMR-1007580-GH0101-1-GCDB",
            ["corr:20250328-MMR-1007580-GH0101-1-GCDB", "corrstem:20250328-MMR-1007580-GH0101"],
        ),
        (
            "https://montandon-eoapi.ifrc.org/stac/collections/usgs-events/items/usgs-event-us7000pn9s",
            ["stac:usgs-events/usgs-event-us7000pn9s", "usgs:us7000pn9s"],
        ),
        ("sentinel-2-l2a/S2A_36PYB_20261027_0_L2A", ["stac:sentinel-2-l2a/S2A_36PYB_20261027_0_L2A"]),
        ("Myanmar earthquake", []),
        ("flood", []),
        ("", []),
    ],
)
def test_parse(text, expected):
    assert R.parse(text) == expected


def test_item_refs_per_source(myanmar):
    by_id = {i["id"]: i for i in myanmar}
    refs = {i: R.item_refs(by_id[i], monty=True) for i in MYANMAR_IDS}
    assert {"glide:EQ-2025-000043-MMR", "glide:EQ-2025-000043"} <= set(refs["glide-event-EQ-2025-000043-MMR"])
    assert "ifrcevent:7464" in refs["ifrcevent-event-7464"]
    assert {"emdat:2025-0223-MMR", "emdat:2025-0223"} <= set(refs["emdat-event-2025-0223-MMR"])
    # CEMS links to the GDACS event explicitly
    assert {"cems:EMSR798", "gdacs:1474477"} <= set(refs["cems-event-EMSR798"])
    assert "corrstem:20250328-MMR-1007580-GH0101" in refs["usgs-event-us7000pn9s"]
    assert R.primary_ref(by_id["cems-event-EMSR798"], monty=True) == "cems:EMSR798"
    # generic catalogs only get stac refs
    assert R.item_refs(by_id["cems-event-EMSR798"], monty=False) == ["stac:cems-events/cems-event-EMSR798"]


def test_corr_stem_drops_the_gdacs_episode():
    assert R.corr_stem("20250328-MMR-1007580-GH0101-1630564-GCDB") == "20250328-MMR-1007580-GH0101"
    assert R.corr_stem("20260901-NPL-GEO-EAR-GRO-1-GCDB") == "20260901-NPL-GEO-EAR-GRO"


# -- grouping -------------------------------------------------------------------
def _cluster(items, seed_id):
    members = [D.member_from_item(i) for i in items]
    seed = next(m for m in members if m.item_id == seed_id)
    return D.cluster(seed, [m for m in members if m is not seed])


def test_every_myanmar_id_groups_the_same_disaster(myanmar):
    groups = []
    for seed_id in MYANMAR_IDS:
        anchor, members, possible, _ = _cluster(myanmar, seed_id)
        assert anchor.ref == "usgs:us7000pn9s" and anchor.magnitude == 7.7
        groups.append({m.ref for m in members})
    assert all(g == groups[0] for g in groups)
    sources = {r.split(":")[0] for r in groups[0]}
    assert {"glide", "ifrcevent", "emdat", "cems", "idmc-idu", "gdacs", "usgs", "pdc"} <= sources


def test_country_level_sources_match_by_day_and_wide_radius(myanmar):
    _, members, _, _ = _cluster(myanmar, "usgs-event-us7000pn9s")
    ifrc = next(m for m in members if m.source == "ifrcevent")
    assert ifrc.coarse and ifrc.score >= D.STRONG and "515 km" in ifrc.reason


def test_unrelated_quakes_in_the_same_country_stay_out(china):
    anchor, members, possible, _ = _cluster(china, "glide-event-EQ-2026-000195-CHN")
    assert anchor.ref == "usgs:us6000u43g"  # USGS wins a magnitude tie with PDC
    assert all(D._km(anchor.point, m.point) < 100 for m in members if m.point and not m.coarse)
    far = [m for m in [*members, *possible] if m.point and D._km(anchor.point, m.point) > 500]
    assert far == []


def test_overrides_remove_and_confirm(myanmar):
    members = [D.member_from_item(i) for i in myanmar]
    seed = next(m for m in members if m.item_id == "glide-event-EQ-2025-000043-MMR")
    rest = [m for m in members if m is not seed]
    _, before, possible, _ = D.cluster(seed, rest)
    maybe = possible[0].ref
    overrides = {"ifrcevent:7464": "different", maybe: "same"}
    _, after, possible2, removed = D.cluster(seed, rest, overrides)
    assert "ifrcevent:7464" not in {m.ref for m in after} and [m.ref for m in removed] == ["ifrcevent:7464"]
    assert maybe in {m.ref for m in after} and maybe not in {m.ref for m in possible2}


# -- resolving ------------------------------------------------------------------
def test_resolve_live_from_any_id_and_cache(fake_monty, montandon):
    d = D.resolve("EQ-2025-000043-MMR")
    assert d.key == "usgs:us7000pn9s" and d.title == "Myanmar Earthquake March 2025"
    assert d.countries == ["MMR"] and "ifrcevent:7464" in d.id_refs
    assert "glide:EQ-2025-000043" not in d.id_refs  # the full GLIDE number is shown instead
    assert ("get", "glide-events", "glide-event-EQ-2025-000043-MMR") in fake_monty.calls
    window = [c for c in fake_monty.calls if c[0] == "search"][-1]
    assert "usgs-hazards" not in window[1] and "MMR" in window[2]
    n = len(fake_monty.calls)
    assert D.resolve("EQ-2025-000043-MMR").key == d.key and len(fake_monty.calls) == n  # cached
    assert D.resolve("go:7464").key == d.key  # src_event_id lookup in ifrcevent-events
    assert D.resolve("EMSR798").key == d.key
    assert D.resolve("EQ-2099-000001-MMR") is None


def test_resolve_prefers_a_stored_item(fake_monty, montandon, myanmar):
    raw = next(i for i in myanmar if i["id"] == "emdat-event-2025-0223-MMR")
    upsert_item(montandon, raw)
    assert D.resolve("2025-0223-MMR").key == "usgs:us7000pn9s"
    assert not any(c[0] == "get" for c in fake_monty.calls)


def test_resolve_falls_back_to_stored_items_when_the_catalog_is_down(fake_monty, montandon, myanmar):
    raw = next(i for i in myanmar if i["id"] == "ifrcevent-event-7464")
    upsert_item(montandon, raw)
    fake_monty.fail = True
    d = D.resolve("go:7464")
    assert d.key == "ifrcevent:7464" and "could not be reached" in d.error
    with pytest.raises(D.DisasterError):
        D.resolve("EMSR798")


def test_runs_for_matches_own_and_item_refs(fake_monty, montandon, myanmar, template, nepal, user):
    item, *_ = upsert_item(montandon, next(i for i in myanmar if i["id"] == "ifrcevent-event-7464"))
    via_item = create_run(template, {"hazard": "earthquake"}, area=nepal["a1"], stac_item=item, user=user)
    via_glide = create_run(
        template, {"hazard": "earthquake"}, area=nepal["a1"], event_refs=["glide:EQ-2025-000043"], user=user
    )
    other = create_run(template, {"hazard": "flood"}, area=nepal["a2"], user=user)
    d = D.resolve("EMSR798")
    assert set(D.runs_for(d)) == {via_item, via_glide} and other not in D.runs_for(d)


def test_stored_items_get_refs_and_backfill_keeps_working(montandon, sample_item):
    item, *_ = upsert_item(montandon, sample_item)
    assert item.event_refs == [
        "stac:reference-events/example-event-npl-earthquake-2026",
        "corr:20260901-NPL-GEO-EAR-GRO-1-GCDB",
        "corrstem:20260901-NPL-GEO-EAR-GRO",
    ]
    # items fetched from APIs that leave `collection` out still get their stac ref
    bare = {k: v for k, v in sample_item.items() if k != "collection"}
    StacItem.objects.all().delete()
    item, *_ = upsert_item(montandon, bare, collection="reference-events")
    assert item.event_refs[0] == "stac:reference-events/example-event-npl-earthquake-2026"


# -- web ------------------------------------------------------------------------
@pytest.fixture
def staff(db):
    from django.contrib.auth import get_user_model

    return get_user_model().objects.create_user("sam", "sam@example.org", "pw", is_staff=True)


def test_disaster_search_redirects_to_the_canonical_page(client, fake_monty):
    r = client.get(reverse("web:disaster-search"), {"q": "EQ-2025-000043-MMR"})
    assert r.status_code == 302 and r.url == "/disasters/usgs:us7000pn9s/"
    r = client.get("/disasters/cems:EMSR798/")
    assert r.status_code == 302 and r.url == "/disasters/usgs:us7000pn9s/"
    r = client.get(r.url)
    body = r.content.decode()
    assert r.status_code == 200 and "Myanmar Earthquake March 2025" in body
    assert "https://go.ifrc.org/emergencies/7464" in body and "Not the same" not in body
    r = client.get(reverse("web:disaster-search"), {"q": "EQ-2099-000001-MMR"})
    assert b"No disaster found" in r.content


def test_disaster_search_free_text_lists_stored_items(client, montandon, sample_item):
    upsert_item(montandon, sample_item)
    r = client.get(reverse("web:disaster-search"), {"q": "Gorkha"})
    assert r.status_code == 200 and b"Earthquake in Nepal" in r.content


def test_browse_treats_a_disaster_id_as_a_filter(client, fake_monty, template, nepal, user):
    linked = create_run(
        template, {"hazard": "earthquake"}, area=nepal["a1"], event_refs=["cems:EMSR798"], user=user
    )
    linked.display_title = "Linked analysis"
    linked.save()
    other = create_run(template, {"hazard": "flood"}, area=nepal["a2"], user=user)
    other.display_title = "Other analysis"
    other.save()
    r = client.get(
        reverse("web:browse"), {"q": "EQ-2025-000043-MMR", "status": "all"}, HTTP_HX_REQUEST="true"
    )
    body = r.content.decode()
    assert "Linked analysis" in body and "Other analysis" not in body
    assert "Myanmar Earthquake March 2025" in body and "/disasters/usgs:us7000pn9s/" in body
    r = client.get(reverse("web:browse"), {"disaster": "go:7464", "status": "all"})
    assert b"Linked analysis" in r.content and b"Other analysis" not in r.content


def test_staff_can_remove_and_restore_a_source(client, fake_monty, montandon, user, staff):
    data = {
        "key": "usgs:us7000pn9s",
        "catalog": montandon.pk,
        "item_ref": "ifrcevent:7464",
        "verdict": "different",
    }
    client.force_login(user)
    assert client.post(reverse("web:disaster-override"), data).status_code == 302
    assert not EventLinkOverride.objects.exists()  # bounced to login
    client.force_login(staff)
    page = client.get("/disasters/usgs:us7000pn9s/")
    assert b"Not the same" in page.content
    client.post(reverse("web:disaster-override"), data)
    d = D.resolve("usgs:us7000pn9s")
    assert "ifrcevent:7464" not in {m.ref for m in d.members} and d.removed
    client.post(reverse("web:disaster-override"), {**data, "verdict": ""})
    assert not EventLinkOverride.objects.exists()
    assert "ifrcevent:7464" in {m.ref for m in D.resolve("usgs:us7000pn9s").members}


def test_link_possibly_related_run(client, fake_monty, template, user, db):
    from factory.catalog.models import AdminArea, Country

    mmr = Country.objects.create(iso3="MMR", name="Myanmar")
    from django.contrib.gis.geos import MultiPolygon, Polygon

    area = AdminArea.objects.create(
        code="MMR",
        name="Myanmar",
        level=0,
        country=mmr,
        geom=MultiPolygon(Polygon(((95, 21), (97, 21), (97, 23), (95, 23), (95, 21))), srid=4326),
    )
    run = create_run(template, {"hazard": "earthquake"}, area=area, user=user)
    NotebookRun.objects.filter(pk=run.pk).update(created_at="2025-04-02T10:00:00Z")
    client.force_login(user)
    page = client.get("/disasters/usgs:us7000pn9s/")
    assert b"Possibly related analyses" in page.content and b"Link to this disaster" in page.content
    r = client.post(reverse("web:disaster-link-run"), {"key": "usgs:us7000pn9s", "run": run.pk})
    assert r.status_code == 302
    run.refresh_from_db()
    assert run.event_refs == ["usgs:us7000pn9s", "glide:EQ-2025-000043-MMR"]
    assert run in D.runs_for(D.resolve("go:7464"))


def test_new_run_from_a_disaster_stores_the_anchor(client, fake_monty, item_template, user):
    client.force_login(user)
    r = client.get(
        reverse("web:disaster-new-run"), {"key": "usgs:us7000pn9s", "template": item_template.slug}
    )
    item = StacItem.objects.get(item_id="usgs-event-us7000pn9s")
    assert r.status_code == 302 and f"stac_item={item.pk}" in r.url and "disaster=usgs%3Aus7000pn9s" in r.url
    form = client.get(r.url)
    assert form.status_code == 200 and b"usgs:us7000pn9s" in form.content


def test_run_disaster_links_can_be_edited(client, template, nepal, user):
    run = create_run(template, {"hazard": "flood"}, area=nepal["a1"], user=user)
    client.force_login(user)
    url = reverse("web:run-disasters", args=[run.pk])
    r = client.post(url, {"event_refs": "EQ-2025-000043-MMR\ngo:7464"})
    assert r.status_code == 200 and b"IFRC GO 7464" in r.content
    run.refresh_from_db()
    assert run.event_refs == ["glide:EQ-2025-000043-MMR", "glide:EQ-2025-000043", "ifrcevent:7464"]
    r = client.post(url, {"event_refs": "not an id"})
    assert b"Not a disaster ID we recognise" in r.content
    assert b"DISASTERS" in client.get(run.get_absolute_url()).content


# -- API ------------------------------------------------------------------------
def test_api_resolve_and_filter_runs(client, fake_monty, template, nepal, user):
    run = create_run(
        template, {"hazard": "earthquake"}, area=nepal["a1"], event_refs=["emdat:2025-0223"], user=user
    )
    r = client.get("/api/disasters/resolve/", {"q": "https://go.ifrc.org/emergencies/7464"})
    data = r.json()
    assert r.status_code == 200 and data["key"] == "usgs:us7000pn9s" and data["runs"] == [str(run.pk)]
    assert data["anchor"]["magnitude"] == 7.7 and len(data["members"]) > 8
    assert client.get("/api/disasters/resolve/", {"q": "hello"}).status_code == 400
    r = client.get("/api/runs/", {"disaster": "EMSR798"})
    assert [x["id"] for x in r.json()["results"]] == [str(run.pk)]
    assert r.json()["results"][0]["disaster_refs"] == ["emdat:2025-0223"]


def test_api_create_run_with_disaster_ids(client, template, nepal, user):
    client.force_login(user)
    body = {"notebook": template.slug, "parameters": {"hazard": "flood"}, "area": nepal["a1"].pk}
    r = client.post(
        "/api/runs/", {**body, "event_refs": ["EMSR798", "go:7464"]}, content_type="application/json"
    )
    assert r.status_code == 202 and r.json()["event_refs"] == ["cems:EMSR798", "ifrcevent:7464"]
    r = client.post("/api/runs/", {**body, "event_refs": ["nope"]}, content_type="application/json")
    assert r.status_code == 400 and "event_refs" in r.json()
