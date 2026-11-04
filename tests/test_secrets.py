"""Template secrets and the kernel environment: allowlist, declare + grant, injection, redaction, pages."""

import json
from pathlib import Path

import nbformat
import pytest
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.urls import reverse

from factory.notebooks import environment
from factory.notebooks import schema_builder as SB
from factory.notebooks import templates as T
from factory.notebooks.models import RunStatus, TemplateSecret
from factory.notebooks.services import RunRequestError, create_run

TOKEN = "tok-3f9a1c77e2"

PARAMS = (
    "hazard='flood'\nthreshold=0.5\nlayers=[]\nverbose=False\narea={}\narea_geojson=''\n"
    "subdivisions_geojson=''\noutput_dir='.'\nrun_id=''\ninputs_dir=''\nstac_item={}\nstac_item_json=''\n"
    "stac_collection=''\nstac_api_url=''\nstac_catalog_profile=''"
)


def _write_template(tmp_path, settings, body: str):
    tdir = tmp_path / "templates" / "exposure"
    tdir.mkdir(parents=True)
    nb = nbformat.v4.new_notebook()
    nb.metadata["kernelspec"] = {"name": "python3", "display_name": "Python 3", "language": "python"}
    nb.cells = [
        nbformat.v4.new_code_cell(PARAMS, metadata={"tags": ["parameters"]}),
        nbformat.v4.new_code_cell(body),
    ]
    nbformat.write(nb, tdir / "notebook.ipynb")
    settings.NOTEBOOK_FACTORY["TEMPLATES_DIR"] = tmp_path / "templates"
    settings.NOTEBOOK_FACTORY["WORK_DIR"] = tmp_path / "work"
    settings.NOTEBOOK_FACTORY["KEEP_WORKDIRS"] = True


def _fake_render(monkeypatch):
    def fake_render(run, ctx, base_url):
        html = ctx.build_dir / "_build" / "html"
        html.mkdir(parents=True)
        (html / "index.html").write_text("<html></html>")
        ctx.html_dir = html
        return html

    monkeypatch.setattr("factory.notebooks.pipeline.renderer.render", fake_render)


def _declare(template, *names):
    T.save_settings(template, {}, {**template.snapshot(), "secret_names": list(names)})
    template.refresh_from_db()


@pytest.fixture
def secret(template):
    s = TemplateSecret.objects.create(name="MAPBOX_TOKEN", value=TOKEN)
    s.templates.add(template)
    return s


# -- environment ------------------------------------------------------------------------
def test_kernel_env_withholds_worker_credentials(monkeypatch, settings):
    monkeypatch.setenv("DATABASE_URL", "postgis://u:p@db/x")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "s3cr3t")
    monkeypatch.setenv("GDAL_DATA", "/usr/share/gdal")
    monkeypatch.setenv("PYTHONSTARTUP", "/tmp/evil.py")
    env = environment.base_env()
    assert "PATH" in env and env["GDAL_DATA"] == "/usr/share/gdal"
    assert not {"DATABASE_URL", "AWS_SECRET_ACCESS_KEY", "PYTHONSTARTUP"} & set(env)

    settings.NOTEBOOK_FACTORY["KERNEL_ENV_PASSTHROUGH"] = ["AWS_*"]
    assert "AWS_SECRET_ACCESS_KEY" in environment.base_env()
    settings.NOTEBOOK_FACTORY["KERNEL_ENV_PASSTHROUGH"] = []
    settings.NOTEBOOK_FACTORY["KERNEL_ENV_INHERIT"] = True
    assert "DATABASE_URL" in environment.base_env()


def test_secret_names_cannot_shadow_run_or_inherited_variables(db):
    for name in ("STAC_API_KEY", "PATH", "GDAL_DATA"):
        with pytest.raises(ValidationError):
            TemplateSecret(name=name, value="x").full_clean()
    with pytest.raises(ValidationError):
        TemplateSecret(name="lower_case", value="x").full_clean()
    with pytest.raises(ValidationError):
        TemplateSecret(name="BOTH", value="x", from_env="Y").full_clean()
    TemplateSecret(name="MAPBOX_TOKEN", value="x").full_clean()


# -- declare + grant ----------------------------------------------------------------------
def test_only_declared_and_granted_secrets_are_injected(template, secret, nepal, monkeypatch):
    other = TemplateSecret.objects.create(name="OTHER_TOKEN", from_env="WORKER_OTHER")
    other.templates.add(template)
    run = create_run(template, {"hazard": "flood"}, area=nepal["root"])
    assert environment.run_secrets(run) == {}  # granted but not declared

    _declare(template, "MAPBOX_TOKEN", "OTHER_TOKEN")
    monkeypatch.setenv("WORKER_OTHER", "from-the-env")
    run = create_run(template, {"hazard": "flood"}, area=nepal["root"])
    assert environment.run_secrets(run) == {"MAPBOX_TOKEN": TOKEN, "OTHER_TOKEN": "from-the-env"}

    monkeypatch.delenv("WORKER_OTHER")
    with pytest.raises(environment.SecretError, match="WORKER_OTHER is not set"):
        environment.run_secrets(run)


def test_runs_are_refused_when_a_declared_secret_is_missing(template, secret, nepal):
    _declare(template, "MAPBOX_TOKEN", "NOT_DEFINED")
    with pytest.raises(RunRequestError, match="NOT_DEFINED"):
        create_run(template, {"hazard": "flood"}, area=nepal["root"])
    TemplateSecret.objects.create(name="NOT_DEFINED", value="v")
    with pytest.raises(RunRequestError, match="not granted"):
        create_run(template, {"hazard": "flood"}, area=nepal["root"])


def test_secret_names_are_versioned_and_read_from_template_yml(template):
    _, snapshot = T.meta_from_yaml({"secrets": ["MAPBOX_TOKEN"]})
    assert snapshot["secret_names"] == ["MAPBOX_TOKEN"]
    _declare(template, "MAPBOX_TOKEN")
    assert template.current_version.secret_names == ["MAPBOX_TOKEN"]
    assert "MAPBOX_TOKEN" in T.to_yaml(template)


# -- pipeline -------------------------------------------------------------------------------
def test_kernel_gets_the_secret_but_not_the_worker_env_and_outputs_are_redacted(
    template, secret, nepal, local_storage, settings, tmp_path, monkeypatch
):
    from factory.notebooks.pipeline.runner import run_pipeline

    _write_template(
        tmp_path,
        settings,
        "import json, os, pathlib\n"
        "print('token is', os.environ['MAPBOX_TOKEN'])\n"
        "pathlib.Path(output_dir, 'env.json').write_text(json.dumps(sorted(os.environ)))\n"
        "pathlib.Path(output_dir, 'leak.txt').write_text(os.environ['MAPBOX_TOKEN'])",
    )
    _fake_render(monkeypatch)
    monkeypatch.setenv("NF_TEST_DB_PASSWORD", "hunter2-db")
    _declare(template, "MAPBOX_TOKEN")
    run = run_pipeline(str(create_run(template, {"hazard": "flood"}, area=nepal["root"]).id))
    assert run.status == RunStatus.PUBLISHED, run.error

    published = local_storage / "runs" / str(run.id)
    keys = set(json.loads((published / "outputs" / "env.json").read_text()))
    assert {"MAPBOX_TOKEN", "NOTEBOOK_RUN_ID", "STAC_API_KEY", "PATH"} <= keys
    assert not {"NF_TEST_DB_PASSWORD", "DATABASE_URL", "SECRET_KEY"} & keys
    assert (published / "outputs" / "leak.txt").read_text() == "[redacted:MAPBOX_TOKEN]"
    notebook = (published / "notebook.ipynb").read_text()
    assert TOKEN not in notebook and "[redacted:MAPBOX_TOKEN]" in notebook
    messages = [e.message for e in run.log_entries.all()]
    assert not any(TOKEN in m for m in messages)
    assert any("redacted secret value(s)" in m and "MAPBOX_TOKEN" in m for m in messages)


def test_a_failing_cell_does_not_leak_the_secret_into_the_error(
    template, secret, nepal, local_storage, settings, tmp_path
):
    from factory.notebooks.pipeline.runner import run_pipeline

    _write_template(
        tmp_path, settings, "import os\nraise ValueError('bad token ' + os.environ['MAPBOX_TOKEN'])"
    )
    _declare(template, "MAPBOX_TOKEN")
    run = run_pipeline(str(create_run(template, {"hazard": "flood"}, area=nepal["root"]).id))
    assert run.status == RunStatus.FAILED
    assert TOKEN not in run.error and "[redacted:MAPBOX_TOKEN]" in run.error
    assert not any(TOKEN in e.message for e in run.log_entries.all())


def test_existing_example_templates_only_read_run_variables():
    """The bundled templates must keep working with the allowlisted environment."""
    root = Path(__file__).resolve().parent.parent / "notebook_templates"
    for path in root.glob("*/notebook.ipynb"):
        nb = nbformat.read(path, as_version=4)
        assert SB.env_reads(nb) <= set(environment.RUN_ENV), path


# -- warnings ---------------------------------------------------------------------------------
def test_warnings_for_env_reads_and_credential_parameters(db):
    nb = nbformat.v4.new_notebook()
    nb.cells = [
        nbformat.v4.new_code_cell(
            "import os\nkey = os.getenv('MAPBOX_TOKEN')\nurl = os.environ['STAC_API_URL']"
        )
    ]
    assert any("MAPBOX_TOKEN" in w for w in SB.env_warnings(nb))
    assert SB.env_warnings(nb, ["MAPBOX_TOKEN"]) == []
    _, warnings = SB.check_schema({"type": "object", "properties": {"api_token": {"type": "string"}}})
    assert any("api_token" in w and "secret" in w for w in warnings)


# -- pages ----------------------------------------------------------------------------------------
@pytest.fixture
def secret_admin(db, django_user_model):
    user = django_user_model.objects.create_user("sa", "sa@example.org", "pw", is_staff=True)
    user.user_permissions.add(*Permission.objects.filter(codename__endswith="templatesecret"))
    return django_user_model.objects.get(pk=user.pk)


def test_secret_pages_need_the_permission_and_never_show_values(
    client, secret, secret_admin, django_user_model
):
    staff = django_user_model.objects.create_user("st", "st@example.org", "pw", is_staff=True)
    client.force_login(staff)
    assert client.get(reverse("web:secret-list")).status_code == 403
    client.force_login(secret_admin)
    assert TOKEN not in client.get(reverse("web:secret-list")).content.decode()
    page = client.get(reverse("web:secret-edit", args=["MAPBOX_TOKEN"])).content.decode()
    assert TOKEN not in page and "A value is stored" in page


def test_secret_form_keeps_switches_and_grants(client, secret, secret_admin, template):
    client.force_login(secret_admin)
    url = reverse("web:secret-edit", args=["MAPBOX_TOKEN"])
    r = client.post(
        url, {"description": "Map tiles", "value": "", "from_env": "", "templates": [template.pk]}
    )
    assert r.status_code == 302
    secret.refresh_from_db()
    assert secret.value == TOKEN and secret.description == "Map tiles"
    client.post(url, {"value": "", "from_env": "MAPBOX_FROM_K8S", "templates": []})
    secret.refresh_from_db()
    assert (secret.value, secret.from_env, secret.templates.count()) == ("", "MAPBOX_FROM_K8S", 0)
    r = client.post(reverse("web:secret-new"), {"name": "PATH", "value": "x"})
    assert r.status_code == 200 and "can&#x27;t be a secret" in r.content.decode()


def test_template_edit_page_shows_secret_status(client, template, secret):
    from django.contrib.auth import get_user_model

    editor = get_user_model().objects.create_user("ed", "ed@example.org", "pw", is_staff=True)
    editor.user_permissions.add(*Permission.objects.filter(codename="change_analysisnotebook"))
    _declare(template, "MAPBOX_TOKEN", "UNKNOWN_ONE")
    client.force_login(editor)
    page = client.get(reverse("web:template-edit", args=[template.slug])).content.decode()
    assert "granted" in page and "no such secret" in page and TOKEN not in page
