import io
import stat
import zipfile

import nbformat
import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from factory.events.matching import upsert_item
from factory.notebooks.models import NotebookRun, RunStatus, TriggerKind
from factory.notebooks.pipeline.runner import run_pipeline
from factory.notebooks.services import retry_run
from factory.notebooks.uploads import UploadError, as_archive, create_upload, inspect_archive


def _notebook(title="Flood report", executed=True) -> bytes:
    nb = nbformat.v4.new_notebook()
    code = nbformat.v4.new_code_cell("print(1)")
    if executed:
        code.execution_count = 1
        code.outputs = [nbformat.v4.new_output("stream", text="1\n")]
    nb.cells = [nbformat.v4.new_markdown_cell(f"# {title}\n\n![map](img/map.png)"), code]
    return nbformat.writes(nb).encode()


def _zip(files: dict[str, bytes]) -> io.BytesIO:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    buf.seek(0)
    return buf


@pytest.fixture
def staff(db):
    return get_user_model().objects.create_user("sam", "sam@example.org", "pw", is_staff=True)


@pytest.fixture
def archive():
    return _zip({"report/report.ipynb": _notebook(), "report/img/map.png": b"PNG", "__MACOSX/._x": b""})


# -- archives ------------------------------------------------------------------
def test_inspect_archive_finds_notebook_title_and_warnings(archive):
    m = inspect_archive(archive)
    assert m.notebooks == ["report/report.ipynb"] and m.notebook == "report/report.ipynb"
    assert m.title == "Flood report" and m.warnings == []
    m = inspect_archive(_zip({"a.ipynb": _notebook(executed=False)}))
    assert m.warnings and "not been executed" in m.warnings[0]


def test_bare_notebook_is_wrapped():
    m = inspect_archive(as_archive(io.BytesIO(_notebook()), "my_report.ipynb"))
    assert m.notebook == "my_report.ipynb"
    with pytest.raises(UploadError, match="zip archive or a .ipynb"):
        as_archive(io.BytesIO(b"hello"), "notes.txt")


def test_several_notebooks_need_a_choice():
    buf = _zip({"a.ipynb": _notebook("A"), "sub/b.ipynb": _notebook("B")})
    m = inspect_archive(buf)
    assert m.notebooks == ["a.ipynb", "sub/b.ipynb"] and m.notebook is None
    assert inspect_archive(buf, "sub/b.ipynb").title == "B"
    with pytest.raises(UploadError, match="not in the archive"):
        inspect_archive(buf, "c.ipynb")
    with pytest.raises(UploadError, match="pick the one"):
        create_upload(buf)


@pytest.mark.parametrize("name", ["../evil.ipynb", "/abs.ipynb", "a/../../b.png"])
def test_unsafe_paths_are_refused(name):
    with pytest.raises(UploadError, match="Unsafe path"):
        inspect_archive(_zip({"ok.ipynb": _notebook(), name: b"x"}))


def test_symlinks_are_refused():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("ok.ipynb", _notebook())
        link = zipfile.ZipInfo("link")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(link, "/etc/passwd")
    with pytest.raises(UploadError, match="Symbolic links"):
        inspect_archive(buf)


def test_size_limits(settings):
    settings.NOTEBOOK_FACTORY["UPLOAD_MAX_UNCOMPRESSED_BYTES"] = 100
    with pytest.raises(UploadError, match="unpacks to more"):
        inspect_archive(_zip({"a.ipynb": _notebook()}))
    settings.NOTEBOOK_FACTORY["UPLOAD_MAX_BYTES"] = 10
    with pytest.raises(UploadError, match="larger than"):
        as_archive(io.BytesIO(_notebook()), "a.ipynb")


def test_invalid_notebook_and_empty_archive():
    with pytest.raises(UploadError, match="not a valid notebook"):
        inspect_archive(_zip({"a.ipynb": b"{not json"}))
    with pytest.raises(UploadError, match="no .ipynb"):
        inspect_archive(_zip({"readme.md": b"hi"}))


# -- runs ----------------------------------------------------------------------
def test_create_upload_stores_run_tags_and_archive(
    archive, local_storage, staff, facets, montandon, sample_item
):
    item = upsert_item(montandon, sample_item)[0]
    run = create_upload(
        archive, summary="One line", area=None, stac_item=item, filters={"use_case": ["uc2"]}, user=staff
    )
    assert run.notebook is None and run.is_upload and run.trigger == TriggerKind.UPLOAD
    assert run.title == "Flood report" and run.upload_path == "report/report.ipynb"
    assert run.filter_values == {"hazard": ["earthquake"], "use_case": ["uc2"]}
    assert {rf.source for rf in run.run_facets.all()} == {"manual", "stac_item"}
    assert (local_storage / "runs" / str(run.id) / "source.zip").exists()
    assert run.notify_emails == ["sam@example.org"]
    assert [s["key"] for s in run.stages()] == ["queued", "fetching", "rendering", "publishing"]


def test_upload_pipeline_renders_without_executing(
    archive, local_storage, staff, settings, tmp_path, monkeypatch
):
    settings.NOTEBOOK_FACTORY["WORK_DIR"] = tmp_path / "work"

    def fake_render(run, ctx, base_url):
        assert (ctx.outputs_dir / "img" / "map.png").exists()  # siblings ship with the notebook
        html = ctx.build_dir / "_build" / "html"
        html.mkdir(parents=True)
        (html / "index.html").write_text(f"<html>{run.title}</html>")
        ctx.html_dir = html
        return html

    def no_execute(*args, **kwargs):
        raise AssertionError("uploads must not be executed")

    monkeypatch.setattr("factory.notebooks.pipeline.renderer.render", fake_render)
    monkeypatch.setattr("factory.notebooks.pipeline.executor.execute", no_execute)
    run = run_pipeline(str(create_upload(archive, title="Custom", user=staff).id))
    assert run.status == RunStatus.PUBLISHED, run.error
    prefix = local_storage / "runs" / str(run.id)
    assert (prefix / "index.html").read_text() == "<html>Custom</html>"
    assert nbformat.read(prefix / "notebook.ipynb", as_version=4).cells[0].source.startswith("# Flood")
    assert (prefix / "outputs" / "img" / "map.png").read_bytes() == b"PNG"
    assert set(run.artifacts) == {"img/map.png", "source.zip"}


def test_retry_copies_the_archive(archive, local_storage, staff, facets):
    run = create_upload(archive, title="T", description="D", filters={"use_case": ["uc1"]}, user=staff)
    new = retry_run(run)
    assert new.pk != run.pk and new.is_upload and new.display_title == "T" and new.description == "D"
    assert new.filter_values == {"use_case": ["uc1"]}
    assert (local_storage / "runs" / str(new.id) / "source.zip").exists()


def test_check_constraint_requires_notebook_for_non_uploads(db):
    from django.db import IntegrityError

    with pytest.raises(IntegrityError):
        NotebookRun.objects.create(notebook=None, trigger=TriggerKind.MANUAL)


# -- web & API -----------------------------------------------------------------
def _upload_file(buf, name="report.zip"):
    return SimpleUploadedFile(name, buf.getvalue(), content_type="application/zip")


def test_upload_page_is_staff_only(client, user, staff, archive, local_storage):
    url = reverse("web:upload-new")
    client.force_login(user)
    assert client.get(url).status_code == 302
    assert client.post(url, {"file": _upload_file(archive)}).status_code == 302
    assert not NotebookRun.objects.exists()
    client.force_login(staff)
    assert client.get(url).status_code == 200


def test_upload_form_creates_run_and_browse_finds_it(client, staff, archive, local_storage, facets, template):
    client.force_login(staff)
    r = client.post(
        reverse("web:upload-new"),
        {"file": _upload_file(archive), "summary": "Rainfall impacts", "facet_use_case": "uc2"},
    )
    run = NotebookRun.objects.get()
    assert r.status_code == 302 and r.url == run.get_absolute_url()
    assert run.filter_values == {"use_case": ["uc2"]}
    run.set_status(RunStatus.PUBLISHED)

    r = client.get(reverse("web:browse"), {"q": "rainfall"})
    assert b"Flood report" in r.content
    r = client.get(reverse("web:browse"), {"template": "_upload", "use_case": "uc2"})
    assert b"Flood report" in r.content
    r = client.get(reverse("web:browse"), {"template": template.slug})
    assert b"Flood report" not in r.content
    r = client.get(reverse("web:browse"), {"sort": "notebook__title"})
    assert r.status_code == 200 and b"Flood report" in r.content
    r = client.get(run.get_absolute_url())
    assert r.status_code == 200 and b"Rainfall impacts" in r.content and b"Uploaded notebook" in r.content


def test_upload_form_errors(client, staff, local_storage):
    client.force_login(staff)
    two = _zip({"a.ipynb": _notebook("A"), "b.ipynb": _notebook("B")})
    r = client.post(reverse("web:upload-new"), {"file": _upload_file(two)})
    assert r.status_code == 200 and b"pick one" in r.content and not NotebookRun.objects.exists()
    two.seek(0)
    r = client.post(reverse("web:upload-new"), {"file": _upload_file(two), "notebook_path": "b.ipynb"})
    assert r.status_code == 302 and NotebookRun.objects.get().title == "B"


def test_upload_inspect_partial(client, staff):
    client.force_login(staff)
    two = _zip({"a.ipynb": _notebook("A"), "b.ipynb": _notebook("B", executed=False)})
    r = client.post(reverse("web:upload-inspect"), {"file": _upload_file(two)})
    assert b"Notebook to publish" in r.content
    two.seek(0)
    r = client.post(reverse("web:upload-inspect"), {"file": _upload_file(two), "notebook_path": "b.ipynb"})
    assert b"\xe2\x80\x9cB\xe2\x80\x9d" in r.content and b"not been executed" in r.content


def test_run_details_edit(client, staff, archive, local_storage):
    run = create_upload(archive, user=staff)
    client.force_login(staff)
    r = client.post(
        reverse("web:run-details", args=[run.id]),
        {"display_title": "New", "summary": "S", "description": "**D**"},
    )
    assert r.status_code == 200 and b"<strong>D</strong>" in r.content
    run.refresh_from_db()
    assert run.title == "New"


def test_api_upload(client, user, staff, archive, local_storage, facets):
    url = reverse("api:run-upload")
    client.force_login(user)
    assert client.post(url, {"file": _upload_file(archive)}).status_code == 403
    client.force_login(staff)
    r = client.post(url, {"file": _upload_file(archive), "title": "API", "filters": '{"use_case": ["uc1"]}'})
    assert r.status_code == 202, r.content
    body = r.json()
    assert body["is_upload"] and body["notebook"] is None and body["title"] == "API"
    assert body["filters"]["use_case"][0]["key"] == "uc1"
    r = client.post(url, {"file": _upload_file(archive), "filters": '{"nope": ["x"]}'})
    assert r.status_code == 400 and "filters" in r.json()
    r = client.get(reverse("api:run-list"), {"search": "API"})
    assert r.json()["count"] == 1
