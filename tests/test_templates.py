"""Uploaded, versioned notebook templates: detection, bundles, versions, pipeline, web, API, import, migration."""

import io
import json
import zipfile

import nbformat
import pytest
import yaml
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from factory.events.models import TriggerRule
from factory.notebooks import bundles
from factory.notebooks import schema_builder as SB
from factory.notebooks import templates as T
from factory.notebooks.loader import sync_templates
from factory.notebooks.models import AnalysisNotebook, RunStatus, SourceKind
from factory.notebooks.pipeline.inputs import RESERVED_PARAMETERS, prepare_inputs
from factory.notebooks.services import create_run
from factory.notebooks.uploads import ArchiveError

PARAMS_CELL = """\
run_id = ""
inputs_dir = ""
output_dir = "outputs"
area = {}
area_geojson = ""
subdivisions_geojson = ""
stac_item = {}
stac_item_json = ""
stac_collection = ""
stac_api_url = ""
stac_catalog_profile = ""
filters = {}
hazard = ""
label = "hello"  # Text shown in the output
buffer_km = 5.0
levels = [1, 2]
show = True
count = 3
lookup = {"a": 1}
"""

BODY = """\
import json, pathlib
import helper
pathlib.Path(output_dir, "out.json").write_text(json.dumps({"label": label, "v": helper.VERSION}))
"""


def _notebook(params=PARAMS_CELL, body=BODY, title="Demo template"):
    nb = nbformat.v4.new_notebook()
    nb.metadata["kernelspec"] = {"name": "python3", "display_name": "Python 3", "language": "python"}
    nb.cells = [
        nbformat.v4.new_markdown_cell(f"# {title}"),
        nbformat.v4.new_code_cell(params, metadata={"tags": ["parameters"]}),
        nbformat.v4.new_code_cell(body),
    ]
    return nb


NB_JSON = nbformat.writes(_notebook())  # once: new cells get random ids


def _zip(files: dict[str, str | bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _bundle_zip(version="1", meta=None, folder="demo/"):
    files = {
        f"{folder}notebook.ipynb": NB_JSON,
        f"{folder}helper.py": f"VERSION = {version!r}\n",
    }
    if meta is not None:
        files[f"{folder}template.yml"] = yaml.safe_dump(meta)
    return _zip(files)


def _upload(data: bytes, name="demo.zip"):
    return SimpleUploadedFile(name, data, content_type="application/zip")


@pytest.fixture
def editor(db, django_user_model):
    user = django_user_model.objects.create_user("ed", "ed@example.org", "pw", is_staff=True)
    user.user_permissions.add(
        *Permission.objects.filter(codename__in=["add_analysisnotebook", "change_analysisnotebook"])
    )
    return django_user_model.objects.get(pk=user.pk)


@pytest.fixture
def cache_dir(settings, tmp_path):
    settings.NOTEBOOK_FACTORY["TEMPLATE_CACHE_DIR"] = tmp_path / "template-cache"
    return tmp_path / "template-cache"


@pytest.fixture
def uploaded(db, facets, local_storage, cache_dir, editor):
    manifest = bundles.inspect_template(_upload(_bundle_zip()), "demo.zip")
    nb, _ = T.create_from_manifest(manifest, user=editor)
    return nb


# -- detection and the row editor ------------------------------------------------------
def test_reserved_parameters_match_prepare_inputs(template, tmp_path, settings):
    from factory.notebooks.pipeline.context import RunContext

    settings.NOTEBOOK_FACTORY["WORK_DIR"] = tmp_path
    template.requires_area = False
    template.save()
    run = create_run(template, {"hazard": "flood"})
    assert set(prepare_inputs(run, RunContext.create(str(run.id)))) == set(RESERVED_PARAMETERS)


def test_detect_parameters_infers_types_and_skips_reserved():
    detected, warnings = SB.detect_parameters(_notebook())
    by_name = {d.name: d for d in detected}
    assert list(by_name) == ["label", "buffer_km", "levels", "show", "count"]
    assert (by_name["label"].type, by_name["label"].default, by_name["label"].description) == (
        "string",
        "hello",
        "Text shown in the output",
    )
    assert by_name["buffer_km"].type == "number" and by_name["count"].type == "integer"
    assert by_name["show"].type == "boolean"
    assert by_name["levels"].type == "array" and by_name["levels"].choices == [1, 2]
    assert any("lookup" in w for w in warnings)


def test_detected_rows_build_a_valid_schema(db):
    detected, _ = SB.detect_parameters(_notebook())
    schema = SB.schema_from_rows(SB.rows_from_detected(detected))
    assert schema["properties"]["levels"] == {
        "type": "array",
        "title": "Levels",
        "items": {"type": "integer", "enum": [1, 2]},
        "default": [1, 2],
    }
    assert schema["properties"]["buffer_km"]["default"] == 5.0
    errors, _ = SB.check_schema(schema)
    assert errors == {}


def test_rows_round_trip_keeps_unknown_keys():
    schema = {
        "type": "object",
        "properties": {
            "hazard": {
                "type": "string",
                "title": "Hazard",
                "enum": ["flood", "cyclone"],
                "default": "flood",
                "x-enum-labels": {"flood": "Riverine flood"},
                "x-widget": "radio",
            },
            "layers": {
                "type": "array",
                "items": {"type": "string", "enum": ["schools"], "x-enum-labels": {"schools": "Schools"}},
                "default": ["schools"],
                "minItems": 1,
            },
            "t": {"type": "number", "minimum": 0, "maximum": 1, "multipleOf": 0.05, "default": 0.5},
        },
        "required": ["hazard"],
    }
    assert SB.schema_from_rows(SB.rows_from_schema(schema)) == schema


def test_check_schema_rejects_reserved_names_and_lists_without_choices(db):
    errors, _ = SB.check_schema(
        {"type": "object", "properties": {"area": {"type": "string"}, "xs": {"type": "array", "items": {}}}}
    )
    assert set(errors) == {"area", "xs"}
    errors, _ = SB.check_schema({"type": "object", "properties": {"n": {"type": "integer", "default": "x"}}})
    assert "n" in errors


def test_filter_warnings_for_unknown_option_keys(facets):
    schema = {"type": "object", "properties": {"hazard": {"type": "string", "enum": ["flood", "volcano"]}}}
    assert any("volcano" in w for w in SB.filter_warnings(schema))


def test_notebook_warnings_flag_missing_modules_and_parameters_cell():
    nb = _notebook(body="import surely_not_installed_pkg\nimport helper\nimport json")
    warnings = SB.notebook_warnings(nb, ["demo/notebook.ipynb", "demo/helper.py"], "demo/notebook.ipynb")
    assert any("surely_not_installed_pkg" in w for w in warnings)
    assert not any("`helper`" in w for w in warnings)
    nb.cells[1].metadata["tags"] = []
    assert any("tagged `parameters`" in w for w in SB.notebook_warnings(nb, [], "notebook.ipynb"))


# -- bundles --------------------------------------------------------------------------------
def test_bundle_hash_is_stable_and_drops_template_yml(tmp_path):
    a = bundles.inspect_template(_upload(_bundle_zip(meta={"title": "A"})), "a.zip")
    b = bundles.inspect_template(_upload(_bundle_zip(meta={"title": "B"})), "b.zip")
    assert a.bundle.sha256 == b.bundle.sha256
    assert a.bundle.files == ["demo/helper.py", "demo/notebook.ipynb"]
    assert a.meta == {"title": "A"} and a.notebook == "demo/notebook.ipynb"
    folder = tmp_path / "demo"
    folder.mkdir()
    (folder / "notebook.ipynb").write_text(nbformat.writes(_notebook()))
    (folder / "helper.py").write_text("VERSION = '1'\n")
    (folder / "template.yml").write_text("title: x\n")
    (folder / "_build").mkdir()
    (folder / "_build" / "junk.html").write_text("x")
    assert bundles.zip_folder(folder).files == ["helper.py", "notebook.ipynb"]


def test_inspect_refuses_unsafe_or_empty_archives():
    with pytest.raises(ArchiveError):
        bundles.inspect_template(_upload(_zip({"../evil.ipynb": "{}"})), "x.zip")
    with pytest.raises(ArchiveError):
        bundles.inspect_template(_upload(_zip({"readme.txt": "hi"})), "x.zip")


def test_inspect_picks_the_notebook_named_by_template_yml():
    data = _zip(
        {
            "t/main.ipynb": nbformat.writes(_notebook()),
            "t/scratch.ipynb": nbformat.writes(_notebook()),
            "t/template.yml": "notebook: main.ipynb\n",
        }
    )
    assert bundles.inspect_template(_upload(data), "x.zip").notebook == "t/main.ipynb"


def test_materialise_caches_by_hash(uploaded, cache_dir):
    root = bundles.materialise(uploaded.current_version)
    assert (root / "demo" / "helper.py").read_text() == "VERSION = '1'\n"
    assert root == cache_dir / uploaded.current_version.sha256
    assert bundles.materialise(uploaded.current_version) == root


# -- versions -------------------------------------------------------------------------------------
def test_upload_creates_an_inactive_template_with_detected_parameters(uploaded):
    assert uploaded.slug == "demo-template" and uploaded.title == "Demo template"
    assert uploaded.source_kind == SourceKind.BUNDLE and not uploaded.is_active
    assert uploaded.notebook_path == "demo/notebook.ipynb"
    assert set(uploaded.parameter_schema["properties"]) == {"label", "buffer_km", "levels", "show", "count"}
    v1 = uploaded.current_version
    assert v1.number == 1 and v1.archive_key.startswith("templates/demo-template/v1-")


def test_template_yml_supplies_settings_filters_and_schema(db, facets, local_storage, editor):
    meta = {
        "title": "From yaml",
        "filters": {"use_case": ["uc1"]},
        "requires_area": False,
        "version": "0.3",
        "parameters": {"type": "object", "properties": {"label": {"type": "string", "default": "x"}}},
    }
    manifest = bundles.inspect_template(_upload(_bundle_zip(meta=meta)), "x.zip")
    nb, warnings = T.create_from_manifest(manifest, user=editor)
    assert (nb.slug, nb.requires_area, nb.version) == ("from-yaml", False, "0.3")
    assert list(nb.parameter_schema["properties"]) == ["label"]
    assert [o.key for o in nb.facet_options.all()] == ["uc1"]
    assert any("buffer_km" in w for w in warnings)  # in the cell, not in template.yml


def test_settings_amend_an_unused_version_then_freeze_it(uploaded, nepal, editor):
    snapshot = {**uploaded.snapshot(), "requires_area": False}
    assert T.save_settings(uploaded, {"is_active": True}, snapshot, user=editor) == "amended"
    uploaded.refresh_from_db()
    assert uploaded.versions.count() == 1 and uploaded.requires_area is False and uploaded.is_active

    create_run(uploaded, {})
    snapshot = {**uploaded.snapshot(), "estimated_runtime_minutes": 9}
    assert T.save_settings(uploaded, {}, snapshot, user=editor) == "new version"
    uploaded.refresh_from_db()
    v2 = uploaded.current_version
    assert v2.number == 2 and v2.archive_key == uploaded.versions.get(number=1).archive_key
    assert uploaded.estimated_runtime_minutes == 9
    assert T.save_settings(uploaded, {"title": "Renamed"}, uploaded.snapshot()) == "unchanged"


def test_new_version_merges_detected_parameters_and_rollback_restores(uploaded, editor):
    nb = _notebook(params=PARAMS_CELL + "extra_flag = False\n")
    data = _zip({"demo/notebook.ipynb": nbformat.writes(nb), "demo/helper.py": "VERSION = '2'\n"})
    manifest = bundles.inspect_template(_upload(data), "v2.zip")
    v2, warnings = T.add_version_from_manifest(uploaded, manifest, user=editor, note="flag")
    uploaded.refresh_from_db()
    assert uploaded.current_version == v2 and "extra_flag" in uploaded.parameter_schema["properties"]
    assert v2.sha256 != uploaded.versions.get(number=1).sha256
    T.set_current(uploaded, uploaded.versions.get(number=1))
    uploaded.refresh_from_db()
    assert "extra_flag" not in uploaded.parameter_schema["properties"]


def test_new_version_reports_trigger_rules_that_no_longer_validate(uploaded, montandon, editor):
    TriggerRule.objects.create(
        name="auto",
        catalog=montandon,
        notebook=uploaded,
        parameter_template={"count": "{{ item.id }}", "show": 1},
    )
    meta = {"parameters": {"type": "object", "properties": {"show": {"type": "string", "enum": ["a"]}}}}
    manifest = bundles.inspect_template(_upload(_bundle_zip(meta=meta)), "v2.zip")
    _, warnings = T.add_version_from_manifest(uploaded, manifest, user=editor)
    rule_warnings = [w for w in warnings if "auto" in w]
    assert len(rule_warnings) == 1 and "show" in rule_warnings[0]
    assert "count" not in rule_warnings[0]  # Jinja values are rendered per item, not checked


def test_delete_only_without_runs(uploaded, local_storage):
    key = uploaded.current_version.archive_key
    uploaded.requires_area = False
    uploaded.save()
    create_run(uploaded, {}, allow_inactive=True)
    with pytest.raises(T.TemplateError):
        T.delete_template(uploaded)
    uploaded.runs.all().delete()
    T.delete_template(uploaded)
    assert not AnalysisNotebook.objects.filter(slug="demo-template").exists()
    assert not (local_storage / key).exists()


def test_export_round_trips_through_template_yml(uploaded):
    data = T.export_bundle(uploaded)
    manifest = bundles.inspect_template(_upload(data), "export.zip")
    assert manifest.meta["title"] == "Demo template" and manifest.meta["active"] is False
    assert manifest.meta["parameters"] == uploaded.parameter_schema
    assert manifest.bundle.sha256 == uploaded.current_version.sha256


# -- pipeline ---------------------------------------------------------------------------------------
def _fake_render(monkeypatch):
    def fake_render(run, ctx, base_url):
        html = ctx.build_dir / "_build" / "html"
        html.mkdir(parents=True)
        (html / "index.html").write_text("<html></html>")
        ctx.html_dir = html
        return html

    monkeypatch.setattr("factory.notebooks.pipeline.renderer.render", fake_render)


def test_queued_run_executes_the_version_it_was_created_with(
    uploaded, nepal, settings, tmp_path, monkeypatch, editor
):
    from factory.notebooks.pipeline.runner import run_pipeline

    settings.NOTEBOOK_FACTORY["WORK_DIR"] = tmp_path / "work"
    _fake_render(monkeypatch)
    run = create_run(uploaded, {"label": "pinned"}, area=nepal["root"], allow_inactive=True)
    manifest = bundles.inspect_template(_upload(_bundle_zip(version="2")), "v2.zip")
    T.add_version_from_manifest(uploaded, manifest, user=editor)

    run = run_pipeline(str(run.id))
    assert run.status == RunStatus.PUBLISHED, run.error
    assert run.template_version.number == 1
    out = json.loads((tmp_path / "published" / "runs" / str(run.id) / "outputs" / "out.json").read_text())
    assert out == {"label": "pinned", "v": "1"}
    assert any("v1" in e.message and "uploaded bundle" in e.message for e in run.log_entries.all())

    run2 = run_pipeline(str(create_run(uploaded, {}, area=nepal["root"], allow_inactive=True).id))
    assert run2.status == RunStatus.PUBLISHED, run2.error
    out = json.loads((tmp_path / "published" / "runs" / str(run2.id) / "outputs" / "out.json").read_text())
    assert out["v"] == "2" and run2.template_version.number == 2


def test_inactive_templates_need_allow_inactive(uploaded, nepal):
    from factory.notebooks.services import RunRequestError

    with pytest.raises(RunRequestError):
        create_run(uploaded, {}, area=nepal["root"])


# -- web ----------------------------------------------------------------------------------------------
def test_template_pages_need_the_template_permission(client, uploaded, django_user_model):
    staff = django_user_model.objects.create_user("st", "st@example.org", "pw", is_staff=True)
    edit = reverse("web:template-edit", args=[uploaded.slug])
    assert client.get(edit).status_code == 302  # to login
    client.force_login(staff)
    assert client.get(edit).status_code == 403
    assert client.get(reverse("web:template-new")).status_code == 403
    assert client.get(reverse("web:template-detail", args=[uploaded.slug])).status_code == 404  # inactive
    assert uploaded.title not in client.get(reverse("web:template-list")).content.decode()


def test_upload_then_edit_through_the_web(client, editor, facets, local_storage, cache_dir):
    client.force_login(editor)
    r = client.post(reverse("web:template-new"), {"file": _upload(_bundle_zip()), "title": "Web demo"})
    assert r.status_code == 302, r.content.decode()[:2000]
    nb = AnalysisNotebook.objects.get(slug="web-demo")
    assert r.url == reverse("web:template-edit", args=[nb.slug])
    page = client.get(r.url).content.decode()
    assert 'name="p-0-name"' in page and "Run form preview" in page

    rows = SB.rows_from_schema(nb.parameter_schema)
    post = {
        "title": "Web demo",
        "summary": "s",
        "description": "",
        "is_active": "on",
        "version": "1.0",
        "requires_area": "",
        "area_levels": "",
        "estimated_runtime_minutes": "2",
        "facet_options": [facets["uc1"].pk],
        "schema_mode": "rows",
        "p-TOTAL_FORMS": str(len(rows)),
        "p-INITIAL_FORMS": str(len(rows)),
        "p-MIN_NUM_FORMS": "0",
        "p-MAX_NUM_FORMS": "1000",
    }
    for i, row in enumerate(rows):
        for key, value in row.items():
            if value in (None, False):
                continue
            post[f"p-{i}-{key}"] = "on" if value is True else value
        if row["name"] == "count":
            post[f"p-{i}-include"] = ""  # drop it
            post.pop(f"p-{i}-include")
    r = client.post(reverse("web:template-edit", args=[nb.slug]), post)
    assert r.status_code == 302, r.content.decode()[:3000]
    nb.refresh_from_db()
    assert nb.is_active and nb.requires_area is False and nb.version == "1.0"
    assert "count" not in nb.parameter_schema["properties"] and nb.versions.count() == 1
    assert [o.key for o in nb.facet_options.all()] == ["uc1"]


def test_schema_editor_actions(client, editor, uploaded):
    client.force_login(editor)
    url = reverse("web:template-schema", args=[uploaded.slug])
    text = yaml.safe_dump(uploaded.parameter_schema)
    r = client.post(url, {"schema_mode": "yaml", "schema_yaml": text, "action": "to_rows"})
    assert 'name="p-0-name"' in r.content.decode()
    r = client.post(
        url, {"schema_mode": "yaml", "schema_yaml": "type: object\nproperties: {area: {type: string}}"}
    )
    assert "injected by the app" in r.content.decode()
    r = client.post(url, {"schema_mode": "yaml", "schema_yaml": "{}", "action": "detect"})
    assert 'value="buffer_km"' in r.content.decode()


def test_new_version_and_rollback_pages(client, editor, uploaded):
    client.force_login(editor)
    r = client.post(
        reverse("web:template-version-new", args=[uploaded.slug]),
        {"file": _upload(_bundle_zip(version="2")), "note": "bump", "make_current": "on"},
    )
    assert r.status_code == 302
    assert uploaded.versions.count() == 2
    page = client.get(reverse("web:template-versions", args=[uploaded.slug])).content.decode()
    assert "bump" in page and "current" in page
    r = client.post(reverse("web:template-version-current", args=[uploaded.slug, 1]))
    uploaded.refresh_from_db()
    assert uploaded.current_version.number == 1
    r = client.get(reverse("web:template-version-download", args=[uploaded.slug, 2]))
    assert r["Content-Type"] == "application/zip"
    assert "demo/template.yml" in zipfile.ZipFile(io.BytesIO(r.content)).namelist()


def test_retry_after_schema_change_shows_a_message(client, editor, uploaded, nepal):
    client.force_login(editor)
    run = create_run(uploaded, {"label": "x"}, area=nepal["root"], allow_inactive=True)
    snapshot = {
        **uploaded.snapshot(),
        "parameter_schema": {"type": "object", "properties": {"label": {"type": "integer"}}},
    }
    T.save_settings(uploaded, {}, snapshot)
    r = client.post(reverse("web:run-retry", args=[run.pk]), follow=True)
    assert "parameters changed" in r.content.decode()


# -- API ------------------------------------------------------------------------------------------------
def test_api_create_version_and_make_current(client, editor, facets, local_storage, user):
    client.force_login(user)
    r = client.post("/api/templates/", {"file": _upload(_bundle_zip())})
    assert r.status_code == 403
    client.force_login(editor)
    r = client.post(
        "/api/templates/",
        {"file": _upload(_bundle_zip()), "slug": "api-demo", "metadata": '{"version": "0.9"}'},
    )
    assert r.status_code == 201, r.content
    assert r.json()["current_version"]["number"] == 1 and r.json()["is_active"] is False
    r = client.patch(
        "/api/templates/api-demo/",
        {"is_active": True, "filters": {"use_case": ["uc2"]}},
        content_type="application/json",
    )
    assert r.status_code == 200 and r.json()["is_active"] and list(r.json()["filters"]) == ["use_case"]
    r = client.post(
        "/api/templates/api-demo/versions/", {"file": _upload(_bundle_zip(version="2")), "note": "ci"}
    )
    assert r.status_code == 201 and r.json()["number"] == 2
    assert [v["number"] for v in client.get("/api/templates/api-demo/versions/").json()] == [2, 1]
    r = client.post("/api/templates/api-demo/versions/1/make-current/")
    assert r.status_code == 200 and r.json()["current_version"]["number"] == 1


# -- sync_templates -----------------------------------------------------------------------------------
def _folder(root, slug="seeded", helper="1", **meta):
    folder = root / slug
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "notebook.ipynb").write_text(NB_JSON)
    (folder / "helper.py").write_text(f"VERSION = {helper!r}\n")
    (folder / "template.yml").write_text(yaml.safe_dump({"title": "Seeded", "requires_area": False, **meta}))
    return folder


def test_sync_creates_versions_only_when_changed(db, facets, local_storage, tmp_path, editor):
    root = tmp_path / "templates"
    _folder(root)
    assert [r.action for r in sync_templates(root)] == ["created"]
    assert [r.action for r in sync_templates(root)] == ["unchanged"]
    _folder(root, helper="2")
    assert [r.action for r in sync_templates(root, dry_run=True)] == ["new version"]
    assert [r.action for r in sync_templates(root)] == ["new version"]
    nb = AnalysisNotebook.objects.get(slug="seeded")
    assert nb.current_version.number == 2

    # A version made in the app is not overwritten without --force, and titles stay put.
    T.save_settings(nb, {"title": "Edited"}, {**nb.snapshot(), "estimated_runtime_minutes": 4}, user=editor)
    nb.versions.filter(pk=nb.current_version_id).update(created_by=editor)
    _folder(root, helper="3")
    result = sync_templates(root)[0]
    assert result.action == "skipped" and "--force" in result.detail
    assert sync_templates(root, force=True)[0].action == "new version"
    nb.refresh_from_db()
    assert nb.title == "Edited" and nb.current_version.number == 3


def test_sync_only_missing_and_convert(db, facets, local_storage, tmp_path):
    root = tmp_path / "templates"
    _folder(root)
    local = AnalysisNotebook.objects.create(
        slug="seeded", title="Old", notebook_path="seeded/notebook.ipynb", source_kind=SourceKind.LOCAL
    )
    assert sync_templates(root, only_missing=True)[0].action == "unchanged"
    assert sync_templates(root)[0].action == "updated (local)"
    assert sync_templates(root, convert=True)[0].action == "converted"
    local.refresh_from_db()
    assert local.source_kind == SourceKind.BUNDLE and local.notebook_path == "notebook.ipynb"


# -- migration --------------------------------------------------------------------------------------------
@pytest.mark.django_db(transaction=True)
def test_migration_bundles_local_templates(settings, tmp_path, local_storage):
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    def migrate(targets):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(targets)
        return executor.loader.project_state(targets).apps

    root = tmp_path / "templates"
    _folder(root, slug="exp")
    settings.NOTEBOOK_FACTORY["TEMPLATES_DIR"] = root
    try:
        old = migrate([("notebooks", "0007_template_versions")])
        Notebook = old.get_model("notebooks", "AnalysisNotebook")
        Run = old.get_model("notebooks", "NotebookRun")
        exp = Notebook.objects.create(
            slug="exp", title="Exp", notebook_path="exp/notebook.ipynb", version="0.2"
        )
        gone = Notebook.objects.create(slug="gone", title="Gone", notebook_path="gone/notebook.ipynb")
        git = Notebook.objects.create(
            slug="remote",
            title="Remote",
            notebook_path="nb.ipynb",
            source_kind="git",
            source_repo="https://x.org/r",
        )
        run = Run.objects.create(notebook=exp)

        new = migrate([("notebooks", "0008_bundle_existing_templates")])
        Notebook = new.get_model("notebooks", "AnalysisNotebook")
        exp = Notebook.objects.get(pk=exp.pk)
        v1 = exp.current_version
        assert (exp.source_kind, exp.notebook_path, v1.number, v1.label) == (
            "bundle",
            "notebook.ipynb",
            1,
            "0.2",
        )
        assert v1.files == ["helper.py", "notebook.ipynb"] and (local_storage / v1.archive_key).is_file()
        gone = Notebook.objects.get(pk=gone.pk)
        assert gone.source_kind == "local" and gone.current_version.archive_key == ""
        git = Notebook.objects.get(pk=git.pk)
        assert git.source_kind == "git" and git.current_version.source_repo == "https://x.org/r"
        assert new.get_model("notebooks", "NotebookRun").objects.get(pk=run.pk).template_version_id is None

        back = migrate([("notebooks", "0007_template_versions")])
        exp = back.get_model("notebooks", "AnalysisNotebook").objects.get(pk=exp.pk)
        assert (exp.source_kind, exp.notebook_path) == ("local", "exp/notebook.ipynb")
    finally:
        migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())
