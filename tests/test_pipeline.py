import json
import os
import sys
import threading
from pathlib import Path

import nbformat
import pytest

from factory.events.matching import upsert_event
from factory.events.writeback import collect_stac, write_back
from factory.notebooks.models import RunStatus, WritebackStatus
from factory.notebooks.pipeline import inputs, publisher
from factory.notebooks.pipeline.context import RunContext
from factory.notebooks.pipeline.runner import run_pipeline
from factory.notebooks.services import create_run


@pytest.fixture
def local_template(settings, tmp_path, template):
    """A real (tiny) template on disk so the pipeline can execute it with papermill."""
    tdir = tmp_path / "templates" / "exposure"
    tdir.mkdir(parents=True)
    nb = nbformat.v4.new_notebook()
    nb.metadata["kernelspec"] = {"name": "python3", "display_name": "Python 3", "language": "python"}
    nb.cells = [
        nbformat.v4.new_code_cell(
            "hazard='flood'\nthreshold=0.5\nlayers=[]\nverbose=False\narea={}\narea_geojson=''\nsubdivisions_geojson=''\noutput_dir='.'\nrun_id=''\ninputs_dir=''\nevent={}\nevent_json=''\nmontandon_stac_url=''",
            metadata={"tags": ["parameters"]},
        ),
        nbformat.v4.new_code_cell(
            "import json, pathlib\nsubs = json.load(open(subdivisions_geojson))\nprint(area['name'], len(subs['features']))\npathlib.Path(output_dir, 'result.json').write_text(json.dumps({'n': len(subs['features']), 'hazard': hazard}))"
        ),
    ]
    nbformat.write(nb, tdir / "notebook.ipynb")
    settings.NOTEBOOK_FACTORY["TEMPLATES_DIR"] = tmp_path / "templates"
    settings.NOTEBOOK_FACTORY["WORK_DIR"] = tmp_path / "work"
    settings.NOTEBOOK_FACTORY["KEEP_WORKDIRS"] = True
    return template


@pytest.fixture
def local_storage(settings, tmp_path):
    from django.core.files.storage import storages

    settings.STORAGES = {
        **settings.STORAGES,
        "published": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
            "OPTIONS": {"location": str(tmp_path / "published"), "base_url": "/published/"},
        },
    }

    def reset():
        storages.__dict__.pop("backends", None)  # cached_property
        storages._backends = None
        storages._storages = {}

    reset()
    yield tmp_path / "published"
    reset()


def test_prepare_inputs_writes_area_and_subdivisions(template, nepal, tmp_path, settings):
    settings.NOTEBOOK_FACTORY["WORK_DIR"] = tmp_path
    run = create_run(template, {"hazard": "flood"}, area=nepal["root"])
    ctx = RunContext.create(str(run.id))
    injected = inputs.prepare_inputs(run, ctx)
    assert injected["area"]["code"] == "NPL" and injected["hazard"] == "flood"
    subs = json.loads(Path(injected["subdivisions_geojson"]).read_text())
    assert len(subs["features"]) == 2  # immediate children (level 1)
    run.parameters["aggregation_level"] = 2
    injected = inputs.prepare_inputs(run, ctx)
    assert len(json.loads(Path(injected["subdivisions_geojson"]).read_text())["features"]) == 4


def test_pipeline_end_to_end_with_stub_renderer(local_template, local_storage, nepal, monkeypatch):
    """papermill executes for real; MyST is stubbed with a one-file site."""

    def fake_render(run, ctx, base_url):
        html = ctx.build_dir / "_build" / "html"
        html.mkdir(parents=True)
        (html / "index.html").write_text(f"<html><body>{run.title} @ {base_url}</body></html>")
        ctx.html_dir = html
        return html

    monkeypatch.setattr("factory.notebooks.pipeline.renderer.render", fake_render)
    run = create_run(local_template, {"hazard": "earthquake"}, area=nepal["root"])
    run = run_pipeline(str(run.id))
    assert run.status == RunStatus.PUBLISHED, run.error
    assert run.output_url.endswith(f"/published/runs/{run.id}/index.html")
    assert "result.json" in run.artifacts
    assert (local_storage / "runs" / str(run.id) / "index.html").exists()
    assert json.loads((local_storage / "runs" / str(run.id) / "outputs" / "result.json").read_text()) == {
        "n": 2,
        "hazard": "earthquake",
    }
    messages = [e.message for e in run.log_entries.all()]
    assert any("cell 2/3" in m for m in messages) and any("published" in m for m in messages)
    assert run.notifications.count() == 0  # nobody to notify


def test_pipeline_failure_is_recorded(local_template, local_storage, nepal, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no myst here")

    monkeypatch.setattr("factory.notebooks.pipeline.renderer.render", boom)
    run = create_run(local_template, {"hazard": "flood"}, area=nepal["a1"], notify_emails=["x@example.org"])
    run = run_pipeline(str(run.id))
    assert run.status == RunStatus.FAILED
    assert "no myst here" in run.error and run.progress["failed_stage"] == "rendering"
    assert run.notifications.filter(email="x@example.org").exists()


def test_base_url_path_uses_public_base(template, nepal, settings, local_storage):
    run = create_run(template, {"hazard": "flood"}, area=nepal["a1"])
    assert publisher.base_url_path(run) == f"/published/runs/{run.id}"
    settings.PUBLISHED_PUBLIC_BASE_URL = "https://cdn.example.org/nb"
    assert publisher.base_url_path(run) == f"/nb/runs/{run.id}"


def test_writeback_skips_when_disabled_and_pushes_when_enabled(
    settings, tmp_path, event_template, nepal, hazards, sample_item
):
    event, *_ = upsert_event(sample_item)
    run = create_run(event_template, {}, area=nepal["root"], event=event)
    out = tmp_path / "outputs"
    (out / "stac" / "items").mkdir(parents=True)
    (out / "stac" / "collection.json").write_text(
        json.dumps({"type": "Collection", "id": "response-prioritisation-x"})
    )
    (out / "stac" / "items" / "a.json").write_text(
        json.dumps({"type": "Feature", "id": "a", "properties": {}})
    )
    assert collect_stac(out)[0]["id"] == "response-prioritisation-x"
    settings.MONTANDON["WRITEBACK_ENABLED"] = False
    assert write_back(run, out) is None

    class FakeMonty:
        def __init__(self):
            self.collections, self.items = [], []

        def upsert_collection(self, c):
            self.collections.append(c)

        def upsert_item(self, cid, item):
            self.items.append((cid, item))

    settings.MONTANDON["WRITEBACK_ENABLED"] = True
    client = FakeMonty()
    assert write_back(run, out, client=client) == "response-prioritisation-x"
    assert client.items[0][1]["links"][0]["rel"] == "about"
    assert run.writeback_status == WritebackStatus.NONE  # runner sets it, not write_back


def test_prepare_notebook_collapses_code_cells(tmp_path):
    from factory.notebooks.pipeline.renderer import _prepare_notebook

    nb = nbformat.v4.new_notebook()
    nb.cells = [
        nbformat.v4.new_markdown_cell("# title"),
        nbformat.v4.new_code_cell("x = 1", metadata={"tags": ["parameters"]}),
        nbformat.v4.new_code_cell("print(x)", metadata={"tags": ["show-input"]}),
    ]
    src, dest = tmp_path / "in.ipynb", tmp_path / "out.ipynb"
    nbformat.write(nb, src)
    _prepare_notebook(src, dest, hide_code=True)
    out = nbformat.read(dest, as_version=4)
    assert "tags" not in out.cells[0].metadata
    assert out.cells[1].metadata["tags"] == ["hide-input", "parameters"]
    assert out.cells[2].metadata["tags"] == ["show-input"]
    _prepare_notebook(src, dest, hide_code=False)
    assert nbformat.read(dest, as_version=4).cells[1].metadata["tags"] == ["parameters"]


def test_concurrent_first_builds_share_the_theme_download(tmp_path):
    """Two builds on an empty theme cache: one downloads, the other must not read it half-written."""
    from factory.notebooks.pipeline.renderer import _run_myst

    # Stand-in for `myst build`: downloads the theme in two steps unless it is complete,
    # and fails like MyST does when it finds a half-written theme.
    fake_myst = tmp_path / "fake_myst.py"
    fake_myst.write_text(
        "import os, sys, time\n"
        "theme = os.path.join(os.environ['CACHE'], 'theme')\n"
        "done = os.path.join(theme, 'server.js')\n"
        "if os.path.exists(theme) and not os.path.exists(done): sys.exit('half-written theme')\n"
        "if not os.path.exists(done):\n"
        "    os.makedirs(theme)\n"
        "    time.sleep(0.5)\n"
        "    open(done, 'w').close()\n"
    )
    cache = tmp_path / "cache"
    cache.mkdir()
    env = {**os.environ, "CACHE": str(cache)}
    codes = []

    def build():
        proc = _run_myst([sys.executable, str(fake_myst)], tmp_path, env, cache, "book-theme", 30)
        codes.append(proc.returncode)

    threads = [threading.Thread(target=build) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert codes == [0, 0]
    assert (cache / ".book-theme.ready").exists()
