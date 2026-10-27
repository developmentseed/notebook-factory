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
