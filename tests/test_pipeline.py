import json
import os
import sys
import threading
from pathlib import Path

import nbformat
import pytest

from factory.events.matching import upsert_item
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
            "hazard='flood'\nthreshold=0.5\nlayers=[]\nverbose=False\narea={}\narea_geojson=''\nsubdivisions_geojson=''\noutput_dir='.'\nrun_id=''\ninputs_dir=''\nstac_item={}\nstac_item_json=''\nstac_collection=''\nstac_api_url=''\nstac_catalog_profile=''",
            metadata={"tags": ["parameters"]},
        ),
        nbformat.v4.new_code_cell(
            "import json, os, pathlib\nsubs = json.load(open(subdivisions_geojson))\nprint(area['name'], len(subs['features']))\npathlib.Path(output_dir, 'result.json').write_text(json.dumps({'n': len(subs['features']), 'hazard': hazard}))\npathlib.Path(output_dir, 'env.json').write_text(json.dumps({k: os.environ.get(k) for k in ('STAC_API_URL', 'STAC_API_KEY', 'NOTEBOOK_RUN_ID')}))\npathlib.Path(output_dir, 'stac.json').write_text(json.dumps({'id': stac_item.get('id'), 'collection': stac_collection, 'url': stac_api_url}))"
        ),
    ]
    nbformat.write(nb, tdir / "notebook.ipynb")
    settings.NOTEBOOK_FACTORY["TEMPLATES_DIR"] = tmp_path / "templates"
    settings.NOTEBOOK_FACTORY["WORK_DIR"] = tmp_path / "work"
    settings.NOTEBOOK_FACTORY["KEEP_WORKDIRS"] = True
    return template


def test_prepare_inputs_writes_area_and_subdivisions(template, nepal, tmp_path, settings):
    settings.NOTEBOOK_FACTORY["WORK_DIR"] = tmp_path
    run = create_run(template, {"hazard": "flood"}, area=nepal["root"])
    ctx = RunContext.create(str(run.id))
    injected = inputs.prepare_inputs(run, ctx)
    assert injected["area"]["code"] == "NPL" and injected["hazard"] == "flood"
    assert injected["filters"] == {"hazard": ["flood"], "use_case": ["uc1"]}
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


def test_pipeline_passes_stac_item_and_catalog_env_to_kernel(
    local_template, local_storage, nepal, hazards, montandon, sample_item, monkeypatch
):
    """The item is injected as parameters; the catalog URL and key reach the kernel only via its env."""

    def fake_render(run, ctx, base_url):
        html = ctx.build_dir / "_build" / "html"
        html.mkdir(parents=True)
        (html / "index.html").write_text("<html></html>")
        ctx.html_dir = html
        return html

    monkeypatch.setattr("factory.notebooks.pipeline.renderer.render", fake_render)
    item, *_ = upsert_item(montandon, sample_item)
    run = create_run(local_template, {"hazard": "flood"}, area=nepal["root"], stac_item=item)
    run = run_pipeline(str(run.id))
    assert run.status == RunStatus.PUBLISHED, run.error
    out = local_storage / "runs" / str(run.id) / "outputs"
    assert json.loads((out / "env.json").read_text()) == {
        "STAC_API_URL": "https://montandon.example.org/stac",
        "STAC_API_KEY": "[redacted:STAC_API_KEY]",  # the kernel got the key; the published copy is redacted
        "NOTEBOOK_RUN_ID": str(run.id),
    }
    assert json.loads((out / "stac.json").read_text()) == {
        "id": "example-event-npl-earthquake-2026",
        "collection": "reference-events",
        "url": "https://montandon.example.org/stac",
    }
    assert "monty-secret" not in (local_storage / "runs" / str(run.id) / "notebook.ipynb").read_text()
    assert "STAC_API_KEY" not in os.environ


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


def test_writeback_targets_the_items_catalog_only_when_enabled(
    tmp_path, item_template, nepal, hazards, montandon, sample_item
):
    item, *_ = upsert_item(montandon, sample_item)
    run = create_run(item_template, {}, area=nepal["root"], stac_item=item)
    out = tmp_path / "outputs"
    (out / "stac" / "items").mkdir(parents=True)
    (out / "stac" / "collection.json").write_text(
        json.dumps({"type": "Collection", "id": "response-prioritisation-x"})
    )
    (out / "stac" / "items" / "a.json").write_text(
        json.dumps({"type": "Feature", "id": "a", "properties": {}})
    )
    assert collect_stac(out)[0]["id"] == "response-prioritisation-x"
    assert write_back(run, out) is None
    assert "write-back disabled for catalog Montandon" in run.log_entries.last().message

    class FakeMonty:
        def __init__(self):
            self.collections, self.items = [], []

        def upsert_collection(self, c):
            self.collections.append(c)

        def upsert_item(self, cid, item):
            self.items.append((cid, item))

    montandon.writeback_enabled = True
    montandon.save()
    run.refresh_from_db()
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
    from factory.notebooks.pipeline.renderer import _run_myst, _template_cache

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
    cache = _template_cache(tmp_path / "cache", "book-theme")
    env = {**os.environ, "CACHE": str(cache)}
    codes = []

    def build():
        proc = _run_myst([sys.executable, str(fake_myst)], tmp_path, env, cache, 30)
        codes.append(proc.returncode)

    threads = [threading.Thread(target=build) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert codes == [0, 0]
    assert (cache / ".ready").exists()


def test_template_cache_per_template(tmp_path):
    """A release-zip URL template gets its own flat cache directory, separate from other templates."""
    from factory.notebooks.pipeline.renderer import _run_myst, _template_cache

    root = tmp_path / "cache"
    url = "https://github.com/jupyter-book/myst-theme/releases/download/myst-to-react%401.4.1/book-theme.zip"
    cache = _template_cache(root, url)
    assert cache.parent == root and cache.is_dir()
    assert cache.name == "myst-to-react-1.4.1-book-theme.zip-75c301e84bb3"
    assert _template_cache(root, url) == cache
    assert _template_cache(root, url.replace("1.4.1", "1.4.2")) != cache
    assert _template_cache(root, "book-theme") != cache

    proc = _run_myst([sys.executable, "-c", ""], tmp_path, dict(os.environ), cache, 30)
    assert proc.returncode == 0
    assert (cache / ".ready").exists()


def test_missing_myst_cli_is_reported(tmp_path):
    from factory.notebooks.pipeline.renderer import RenderError, _run_myst, _template_cache

    cache = _template_cache(tmp_path, "book-theme")
    with pytest.raises(RenderError, match="MyST CLI not found"):
        _run_myst([str(tmp_path / "no-such-myst")], tmp_path, dict(os.environ), cache, 30)
    assert not (cache / ".ready").exists()


def test_render_links_the_template_cache_and_sets_the_theme(template, nepal, settings, tmp_path, monkeypatch):
    """render() points `_build/templates` at the per-template cache and writes the theme into myst.yml."""
    import yaml

    from factory.notebooks.pipeline.renderer import _template_cache, render

    # Stand-in for `myst build`: writes the theme through `_build/templates` and a one-file site.
    fake_myst = tmp_path / "fake_myst.py"
    fake_myst.write_text(
        "import os\n"
        "open(os.path.join('_build', 'templates', 'template.yml'), 'w').close()\n"
        "os.makedirs(os.path.join('_build', 'html'))\n"
        "open(os.path.join('_build', 'html', 'index.html'), 'w').write('<h1>ok</h1>')\n"
        "print('Built 1 page')\n"
    )
    root = tmp_path / "theme-cache"
    monkeypatch.setitem(settings.NOTEBOOK_FACTORY, "WORK_DIR", tmp_path / "work")
    monkeypatch.setitem(settings.NOTEBOOK_FACTORY, "MYST_TEMPLATE_CACHE_DIR", root)
    monkeypatch.setitem(settings.NOTEBOOK_FACTORY, "MYST_COMMAND", [sys.executable, str(fake_myst)])
    template_url = settings.NOTEBOOK_FACTORY["MYST_TEMPLATE"]
    run = create_run(template, {"hazard": "flood"}, area=nepal["a1"])
    ctx = RunContext.create(str(run.id))
    ctx.executed_path = ctx.workdir / "executed.ipynb"
    nbformat.write(nbformat.v4.new_notebook(cells=[nbformat.v4.new_markdown_cell("# hi")]), ctx.executed_path)

    html = render(run, ctx, f"/published/runs/{run.id}")

    cache = _template_cache(root, template_url)
    link = ctx.build_dir / "_build" / "templates"
    assert link.is_symlink() and link.resolve() == cache.resolve()
    assert (cache / "template.yml").exists() and (cache / ".ready").exists()
    assert yaml.safe_load((ctx.build_dir / "myst.yml").read_text())["site"]["template"] == template_url
    assert (html / "index.html").exists()


# Stand-in for a first `myst build` that leaves a partial theme behind (as an interrupted download
# or a failed `npm ci` does) and then fails, optionally after hanging.
PARTIAL_THEME_MYST = (
    "import os, sys, time\n"
    "cache = os.environ['CACHE']\n"
    "open(os.path.join(cache, 'template.yml'), 'w').close()\n"
    "os.makedirs(os.path.join(cache, 'node_modules', 'pkg'))\n"
    "if sys.argv[1:] == ['hang']: time.sleep(30)\n"
    "sys.exit('npm ci failed')\n"
)


def test_failed_first_build_empties_the_template_cache(tmp_path):
    from factory.notebooks.pipeline.renderer import _run_myst, _template_cache

    fake_myst = tmp_path / "fake_myst.py"
    fake_myst.write_text(PARTIAL_THEME_MYST)
    cache = _template_cache(tmp_path / "cache", "book-theme")
    env = {**os.environ, "CACHE": str(cache)}

    proc = _run_myst([sys.executable, str(fake_myst)], tmp_path, env, cache, 30)

    assert proc.returncode == 1 and "npm ci failed" in proc.stderr
    assert [p.name for p in cache.iterdir()] == [".lock"]


def test_timed_out_first_build_empties_the_template_cache(tmp_path):
    import subprocess

    from factory.notebooks.pipeline.renderer import _run_myst, _template_cache

    fake_myst = tmp_path / "fake_myst.py"
    fake_myst.write_text(PARTIAL_THEME_MYST)
    cache = _template_cache(tmp_path / "cache", "book-theme")
    env = {**os.environ, "CACHE": str(cache)}

    with pytest.raises(subprocess.TimeoutExpired):
        _run_myst([sys.executable, str(fake_myst), "hang"], tmp_path, env, cache, 2)

    assert [p.name for p in cache.iterdir()] == [".lock"]


def test_build_that_waited_for_a_finished_theme_runs_without_the_lock(tmp_path, monkeypatch):
    """Another build finishes the theme while this one waits for the lock; this one must not hold it."""
    import fcntl

    from factory.notebooks.pipeline import renderer

    cache = renderer._template_cache(tmp_path / "cache", "book-theme")
    real_flock = fcntl.flock

    def flock_after_other_build(fd, op):
        (cache / ".ready").touch()
        return real_flock(fd, op)

    monkeypatch.setattr(renderer.fcntl, "flock", flock_after_other_build)
    # Stand-in for `myst build`: reports whether the theme lock is free while it runs.
    fake_myst = tmp_path / "fake_myst.py"
    fake_myst.write_text(
        "import fcntl, os\n"
        "with open(os.path.join(os.environ['CACHE'], '.lock'), 'w') as lock:\n"
        "    try:\n"
        "        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
        "        print('free')\n"
        "    except BlockingIOError:\n"
        "        print('held')\n"
    )
    env = {**os.environ, "CACHE": str(cache)}

    proc = renderer._run_myst([sys.executable, str(fake_myst)], tmp_path, env, cache, 30)

    assert proc.returncode == 0 and proc.stdout.strip() == "free"
