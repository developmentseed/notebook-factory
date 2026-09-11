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


def test_events_pages_and_notifications(client, user, hazards, sample_item, published_run):
    from factory.events.matching import upsert_event
    from factory.notifications.services import notify_run_finished

    event, *_ = upsert_event(sample_item)
    assert client.get(reverse("web:event-list")).status_code == 200
    assert client.get(event.get_absolute_url()).status_code == 200
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
