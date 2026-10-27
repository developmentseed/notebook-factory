import pytest
from django.urls import reverse

from factory.catalog.models import AdminArea
from factory.notebooks.models import NotebookRun, RunStatus
from factory.notebooks.services import create_run


@pytest.fixture
def published_run(template, nepal, user):
    run = create_run(template, {"hazard": "flood"}, area=nepal["a1"], user=user)
    run.output_url = "http://storage/runs/x/index.html"
    run.notebook_url = "http://storage/runs/x/notebook.ipynb"
    run.save()
    run.set_status(RunStatus.PUBLISHED)
    return run


def test_browse_lists_published_runs_and_filters(client, published_run, nepal):
    r = client.get(reverse("web:browse"))
    assert r.status_code == 200 and b"Exposure" in r.content and b"West" in r.content
    r = client.get(reverse("web:browse"), {"hazard": "earthquake"})
    assert b"No analyses match" in r.content
    r = client.get(reverse("web:browse"), {"country": "NPL"}, HTTP_HX_REQUEST="true")
    assert r.status_code == 200 and b"<html" not in r.content and b"West" in r.content


def test_help_page(client):
    r = client.get(reverse("web:help"))
    assert r.status_code == 200
    for anchor in (
        b'id="browsing-analyses"',
        b'id="importing-countries-and-admin-boundaries"',
        b'id="using-the-api"',
    ):
        assert anchor in r.content
    assert (
        b'class="toc"' in r.content
        and b"{{BASE_URL}}" not in r.content
        and b"http://testserver/api" in r.content
    )


def test_template_pages(client, template):
    assert client.get(reverse("web:template-list")).status_code == 200
    r = client.get(template.get_absolute_url())
    assert r.status_code == 200 and b"threshold" in r.content


def test_run_pages(client, published_run):
    assert client.get(published_run.get_absolute_url()).status_code == 200
    r = client.get(reverse("web:run-view", args=[published_run.id]))
    assert r.status_code == 200 and b"<iframe" in r.content
    r = client.get(reverse("web:run-status", args=[published_run.id]))
    assert r.json()["status"] == "published"


def test_run_wizard_requires_login_and_creates_runs(client, user, template, nepal):
    url = reverse("web:run-new", args=[template.slug])
    assert client.get(url).status_code == 302
    client.force_login(user)
    assert client.get(url).status_code == 200
    a = AdminArea.objects.get(code="NPL.1_1")
    r = client.post(
        url,
        {
            "country": "NPL",
            "level": 1,
            "area_ids": str(a.pk),
            "hazard": "earthquake",
            "threshold": "0.6",
            "layers": ["schools", "hospitals"],
            "notify_emails": "x@example.org",
        },
    )
    assert r.status_code == 302
    run = NotebookRun.objects.get()
    assert run.area == a and run.parameters == {
        "hazard": "earthquake",
        "threshold": 0.6,
        "layers": ["schools", "hospitals"],
        "verbose": False,
    }
    assert set(run.notify_emails) == {"x@example.org", "ana@example.org"}
    # fan out over a whole level
    r = client.post(url, {"country": "NPL", "level": 2, "all_at_level": "on", "hazard": "flood"})
    assert r.status_code == 302 and "/batches/" in r["Location"]
    assert NotebookRun.objects.count() == 5


def test_run_wizard_reports_parameter_errors(client, user, template, nepal):
    client.force_login(user)
    a = AdminArea.objects.get(code="NPL.1_1")
    r = client.post(
        reverse("web:run-new", args=[template.slug]),
        {"country": "NPL", "level": 1, "area_ids": str(a.pk), "hazard": "flood", "threshold": "7"},
    )
    assert r.status_code == 200 and b"greater than the maximum" in r.content
    assert NotebookRun.objects.count() == 0


def test_api_templates_runs_and_areas(client, published_run, nepal):
    r = client.get("/api/templates/")
    assert r.status_code == 200 and r.json()["results"][0]["slug"] == "exposure"
    r = client.get("/api/runs/", {"country": "NPL", "status": "published"})
    assert r.json()["count"] == 1 and r.json()["results"][0]["output_url"].endswith("index.html")
    r = client.get(f"/api/runs/{published_run.id}/")
    assert "log" in r.json()
    r = client.get("/api/areas/geojson/", {"country": "NPL", "level": 2})
    gj = r.json()
    assert (
        gj["type"] == "FeatureCollection"
        and len(gj["features"]) == 4
        and gj["features"][0]["geometry"]["type"] in ("Polygon", "MultiPolygon")
    )
    assert client.get("/api/areas/geojson/").status_code == 400


def test_api_create_run_and_batch(client, user, template, nepal):
    assert (
        client.post("/api/runs/", {"notebook": "exposure"}, content_type="application/json").status_code
        == 403
    )
    client.force_login(user)
    a = AdminArea.objects.get(code="NPL.1_1")
    r = client.post(
        "/api/runs/",
        {"notebook": "exposure", "area": a.pk, "parameters": {"hazard": "flood"}},
        content_type="application/json",
    )
    assert r.status_code == 202 and r.json()["status"] == "queued" and r.json()["trigger"] == "api"
    r = client.post(
        "/api/runs/",
        {"notebook": "exposure", "country": "NPL", "area_level": 2, "parameters": {"hazard": "flood"}},
        content_type="application/json",
    )
    assert r.status_code == 202 and r.json()["status_counts"]["queued"] == 4
    r = client.post(
        "/api/runs/",
        {"notebook": "exposure", "area": a.pk, "parameters": {"hazard": "nope"}},
        content_type="application/json",
    )
    assert r.status_code == 400 and "hazard" in r.json()["parameters"]


def test_stac_item_pages_and_notifications(client, user, hazards, montandon, sample_item, published_run):
    from factory.events.matching import upsert_item
    from factory.notifications.services import notify_run_finished

    item, *_ = upsert_item(montandon, sample_item)
    r = client.get(reverse("web:stac-item-list"), {"catalog": "montandon"})
    assert r.status_code == 200 and b"Earthquake in Nepal" in r.content
    r = client.get(item.get_absolute_url())
    assert r.status_code == 200 and b"20260901-NPL-GEO-EAR-GRO-1-GCDB" in r.content
    notes = notify_run_finished(published_run)
    assert len(notes) == 1 and notes[0].user == user and notes[0].sent_at is not None
    client.force_login(user)
    r = client.get(reverse("web:notifications"))
    assert b"Analysis ready" in r.content
    r = client.get(reverse("web:notification-open", args=[notes[0].pk]))
    assert r.status_code == 302
    notes[0].refresh_from_db()
    assert notes[0].read_at is not None


def test_published_files_are_embeddable(client, settings, tmp_path):
    settings.PUBLISHED_ROOT = str(tmp_path)
    (tmp_path / "runs" / "abc").mkdir(parents=True)
    (tmp_path / "runs" / "abc" / "index.html").write_text("<h1>hi</h1>")
    for url in ("/published/runs/abc/index.html", "/published/runs/abc/", "/published/runs/abc"):
        r = client.get(url)
        assert r.status_code == 200 and "X-Frame-Options" not in r, url
    r = client.get("/published/runs/abc/missing.js")
    assert r.status_code == 404 and "X-Frame-Options" not in r
    assert client.get("/published/../manage.py").status_code == 404


# ---------------------------------------------------------------------------
# STAC catalogs, picker and runs with STAC items
# ---------------------------------------------------------------------------


@pytest.fixture
def staff(db):
    from django.contrib.auth import get_user_model

    return get_user_model().objects.create_user("sam", "sam@example.org", "pw", is_staff=True)


class FakeStac:
    """Stands in for StacClient in the web layer."""

    def __init__(self, items, collections=("reference-events", "sentinel-2-l2a")):
        self.items = items
        self._collections = collections
        self.searches = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def root(self):
        return {"id": "fake", "title": "Fake STAC"}

    def collections(self):
        return [{"id": c, "title": c.title()} for c in self._collections]

    def search(self, collections, **kwargs):
        self.searches.append((collections, kwargs))
        found = [i for i in self.items if i["collection"] in collections]
        if kwargs.get("ids"):
            found = [i for i in found if i["id"] in kwargs["ids"]]
        return iter(found)

    def get_item(self, collection, item_id):
        for i in self.items:
            if i["collection"] == collection and i["id"] == item_id:
                return i
        from factory.events.stac_client import StacClientError

        raise StacClientError(f"GET {item_id} -> 404")


@pytest.fixture
def fake_stac(monkeypatch, sample_item):
    from factory.events.models import StacCatalog

    fake = FakeStac([sample_item])
    monkeypatch.setattr(StacCatalog, "client", lambda self: fake)
    monkeypatch.setattr("factory.web.views.StacClient", lambda url, key: fake)
    return fake


def test_catalog_pages_are_staff_only(client, user, staff, montandon):
    client.force_login(user)
    assert client.get(reverse("web:catalog-list")).status_code == 302
    client.force_login(staff)
    r = client.get(reverse("web:catalog-list"))
    assert r.status_code == 200 and b"Montandon" in r.content and b"monty-secret" not in r.content
    r = client.get(reverse("web:catalog-edit", args=["montandon"]))
    assert r.status_code == 200 and b"monty-secret" not in r.content and b"A key is stored" in r.content


def test_catalog_create_edit_keeps_key_and_delete(client, staff):
    from factory.events.models import StacCatalog

    client.force_login(staff)
    data = {"name": "Earth Search", "slug": "es", "url": "https://es.example.org/v1", "api_key": "k1"}
    data.update(profile="generic", is_active="on")
    assert client.post(reverse("web:catalog-new"), data).status_code == 302
    cat = StacCatalog.objects.get(slug="es")
    assert cat.api_key == "k1"
    client.post(reverse("web:catalog-edit", args=["es"]), {**data, "api_key": "", "name": "ES"})
    cat.refresh_from_db()
    assert cat.name == "ES" and cat.api_key == "k1"  # blank keeps the stored key
    client.post(reverse("web:catalog-edit", args=["es"]), {**data, "api_key": "", "clear_api_key": "on"})
    cat.refresh_from_db()
    assert cat.api_key == ""
    client.post(reverse("web:catalog-delete", args=["es"]))
    assert not StacCatalog.objects.filter(slug="es").exists()


def test_catalog_test_connection(client, staff, fake_stac):
    client.force_login(staff)
    r = client.post(reverse("web:catalog-test"), {"url": "https://x.org/v1"})
    assert b"Connected" in r.content and b"Fake STAC" in r.content and b"reference-events" in r.content


def test_stac_picker_applies_soft_hints(client, user, item_template, montandon, earth_search, fake_stac):
    client.force_login(user)
    url = reverse("web:stac-picker", args=[item_template.slug])
    r = client.get(url)
    body = r.content.decode()
    # profile hint → only the Montandon catalog; collections hint → only reference-events, preselected
    assert "Earth Search" not in body and "sentinel-2-l2a" not in body
    assert 'value="reference-events" selected' in body and "example-event-npl-earthquake-2026" in body
    r = client.get(url, {"stac_show_all": "1", "stac_catalog": montandon.pk})
    body = r.content.decode()
    assert "Earth Search" in body and "sentinel-2-l2a" in body
    r = client.get(
        url, {"stac_catalog": montandon.pk, "stac_collection": "reference-events", "stac_q": "nope"}
    )
    assert "No item with id" in r.content.decode()
    assert fake_stac.searches[-1][1]["ids"] == ["nope"]


def test_run_form_fetches_picked_item(client, user, nepal, hazards, item_template, montandon, fake_stac):
    from factory.events.models import StacItem

    client.force_login(user)
    url = reverse("web:run-new", args=[item_template.slug])
    assert client.get(url).status_code == 200
    data = {
        "area_ids": str(nepal["root"].pk),
        "country": "NPL",
        "level": 0,
        "radius": 5,
        "stac_catalog": montandon.pk,
        "stac_collection": "reference-events",
        "stac_item_id": "example-event-npl-earthquake-2026",
    }
    r = client.post(url, data)
    assert r.status_code == 302, r.content.decode()[:2000]
    run = NotebookRun.objects.get()
    assert run.stac_item.item_id == "example-event-npl-earthquake-2026" and run.stac_item.catalog == montandon
    assert StacItem.objects.count() == 1
    # prefill from an item page goes straight to that item
    r = client.get(url, {"stac_item": run.stac_item.pk})
    assert b'value="example-event-npl-earthquake-2026"' in r.content
    # unknown item → form error, no run
    r = client.post(url, {**data, "stac_item_id": "missing"})
    assert r.status_code == 200 and b"Could not fetch missing" in r.content
    r = client.post(url, {**data, "stac_item_id": ""})
    assert b"needs a STAC item" in r.content


def test_api_catalogs_hide_key_and_require_staff(client, user, staff, montandon):
    client.force_login(user)
    r = client.get("/api/catalogs/")
    assert r.status_code == 200 and r.json()[0]["has_api_key"] is True
    assert "monty-secret" not in r.content.decode()
    body = {"name": "X", "slug": "x", "url": "https://x.org/v1", "api_key": "s3cret"}
    assert client.post("/api/catalogs/", body, content_type="application/json").status_code == 403
    client.force_login(staff)
    r = client.post("/api/catalogs/", body, content_type="application/json")
    assert r.status_code == 201 and "s3cret" not in r.content.decode()


def test_api_create_run_with_live_stac_item(
    client, user, nepal, hazards, item_template, montandon, fake_stac
):
    client.force_login(user)
    r = client.post(
        "/api/runs/",
        {
            "notebook": item_template.slug,
            "area": nepal["root"].pk,
            "stac_catalog": "montandon",
            "stac_collection": "reference-events",
            "stac_item_id": "example-event-npl-earthquake-2026",
        },
        content_type="application/json",
    )
    assert r.status_code == 202, r.json()
    assert r.json()["stac_item"]["catalog"] == "montandon"
    assert "monty-secret" not in r.content.decode()
    item_pk = r.json()["stac_item"]["id"]
    r = client.get("/api/runs/", {"stac_item": item_pk})
    assert r.json()["count"] == 1
    r = client.post(
        "/api/runs/",
        {"notebook": item_template.slug, "area": nepal["root"].pk, "stac_catalog": "montandon"},
        content_type="application/json",
    )
    assert r.status_code == 400 and "stac_item_id" in r.json()


# ---------------------------------------------------------------------------
# Filters (facets)
# ---------------------------------------------------------------------------


def test_browse_filters_by_any_facet(client, published_run, template, nepal, facets):
    other = create_run(template, {"hazard": "earthquake"}, area=nepal["a2"])
    other.set_status(RunStatus.PUBLISHED)
    r = client.get(reverse("web:browse"))
    body = r.content.decode()
    assert 'name="hazard" value="flood"' in body and 'name="use_case" value="uc1"' in body
    r = client.get(reverse("web:browse"), {"hazard": "earthquake"}, HTTP_HX_REQUEST="true")
    assert b"East" in r.content and b"West" not in r.content
    r = client.get(reverse("web:browse"), {"hazard": "flood", "use_case": "uc2"}, HTTP_HX_REQUEST="true")
    assert b"No analyses match" in r.content


def test_filters_page_is_staff_only_and_saves_options(client, user, staff, facets):
    from factory.catalog.models import Facet

    client.force_login(user)
    assert client.get(reverse("web:facet-list")).status_code == 302
    client.force_login(staff)
    r = client.get(reverse("web:facet-list"))
    assert r.status_code == 200 and b"Hazard" in r.content
    data = {
        "label": "Sector",
        "key": "sector",
        "multiple": "on",
        "show_in_browse": "on",
        "order": 30,
        "options-TOTAL_FORMS": 3,
        "options-INITIAL_FORMS": 0,
        "options-0-label": "Health",
        "options-0-key": "health",
        "options-0-match_codes": "hea, med",
        "options-0-order": 1,
        "options-1-label": "",
        "options-1-key": "",
        "options-1-order": 100,
        "options-2-order": 100,
    }
    r = client.post(reverse("web:facet-new"), data)
    assert r.status_code == 302, r.content.decode()[:3000]
    sector = Facet.objects.get(key="sector")
    assert sector.multiple and list(sector.options.values_list("key", "match_codes")) == [
        ("health", ["hea", "med"])
    ]
    r = client.post(reverse("web:facet-new"), {**data, "key": "status", "options-0-key": "x"})
    assert r.status_code == 200 and b"reserved" in r.content
    client.post(reverse("web:facet-delete", args=["sector"]))
    assert not Facet.objects.filter(key="sector").exists()


def test_facet_in_use_cannot_be_deleted(client, staff, published_run):
    from factory.catalog.models import Facet

    client.force_login(staff)
    r = client.post(reverse("web:facet-delete", args=["hazard"]), follow=True)
    assert Facet.objects.filter(key="hazard").exists() and b"not deleted" in r.content


def test_run_form_offers_unpinned_facets_and_saves_manual_tags(client, user, nepal, template, facets):
    from factory.catalog.models import Facet, FacetOption

    sector = Facet.objects.create(key="sector", label="Sector", multiple=True)
    FacetOption.objects.create(facet=sector, key="health", label="Health")
    client.force_login(user)
    url = reverse("web:run-new", args=[template.slug])
    body = client.get(url).content.decode()
    # hazard comes from the parameter and use case is pinned by the template: neither is asked
    assert 'name="facet_sector"' in body and 'name="facet_hazard"' not in body
    assert 'name="facet_use_case"' not in body and "Risk exposure" in body
    r = client.post(
        url,
        {
            "area_ids": str(nepal["a1"].pk),
            "country": "NPL",
            "level": 1,
            "hazard": "flood",
            "facet_sector": "health",
        },
    )
    assert r.status_code == 302
    assert NotebookRun.objects.get().filter_values == {
        "hazard": ["flood"],
        "use_case": ["uc1"],
        "sector": ["health"],
    }


def test_run_tags_can_be_edited_by_requester(client, user, staff, published_run):
    from django.contrib.auth import get_user_model

    url = reverse("web:run-tags", args=[published_run.id])
    stranger = get_user_model().objects.create_user("bo", "bo@example.org", "pw")
    client.force_login(stranger)
    assert client.get(url).status_code == 403
    client.force_login(user)
    r = client.get(published_run.get_absolute_url())
    assert b"Edit tags" in r.content
    assert b'name="facet_hazard"' in client.get(url).content
    r = client.post(url, {"facet_hazard": "earthquake"})
    assert r.status_code == 200 and b"Earthquake" in r.content
    published_run.refresh_from_db()
    assert published_run.filter_values["hazard"] == ["earthquake"]


def test_api_filters_and_run_filtering(client, user, staff, published_run, template, nepal, facets):
    r = client.get("/api/filters/")
    hazard = next(f for f in r.json() if f["key"] == "hazard")
    assert {o["key"] for o in hazard["options"]} >= {"flood", "earthquake"}
    assert client.post("/api/filters/", {"key": "x", "label": "X"}).status_code in (401, 403)
    run = client.get(f"/api/runs/{published_run.id}/").json()
    assert run["filters"]["hazard"][0]["key"] == "flood" and run["filters"]["use_case"][0]["key"] == "uc1"
    assert client.get("/api/runs/", {"hazard": "flood", "use_case": "uc1"}).json()["count"] == 1
    assert client.get("/api/runs/", {"hazard": "earthquake"}).json()["count"] == 0
    tpl = client.get(f"/api/templates/{template.slug}/").json()
    assert {o["key"] for o in tpl["filters"]["hazard"]} == {"flood", "earthquake"}
    assert client.get("/api/templates/", {"use_case": "uc2"}).json()["count"] == 0
    client.force_login(user)
    r = client.post(
        "/api/runs/",
        {
            "notebook": "exposure",
            "area": nepal["a2"].pk,
            "parameters": {"hazard": "flood"},
            "filters": {"hazard": ["earthquake"]},
        },
        content_type="application/json",
    )
    assert r.status_code == 202 and r.json()["filters"]["hazard"][0]["key"] == "earthquake"
    r = client.post(
        "/api/runs/",
        {
            "notebook": "exposure",
            "area": nepal["a2"].pk,
            "parameters": {"hazard": "flood"},
            "filters": {"nope": ["x"]},
        },
        content_type="application/json",
    )
    assert r.status_code == 400 and "filters" in r.json()
