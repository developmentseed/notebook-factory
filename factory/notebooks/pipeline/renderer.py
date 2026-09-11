"""Render an executed notebook to a static HTML site with MyST (mystmd)."""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

import nbformat
import yaml
from django.conf import settings

from ..models import NotebookRun
from .context import RunContext

log = logging.getLogger(__name__)


class RenderError(RuntimeError):
    pass


def _myst_cmd() -> list[str]:
    cmd = settings.NOTEBOOK_FACTORY["MYST_COMMAND"]
    return cmd.split() if isinstance(cmd, str) else list(cmd)


def myst_version() -> str | None:
    try:
        out = subprocess.run([*_myst_cmd(), "--version"], capture_output=True, text=True, timeout=60)
        m = re.search(r"(\d+\.\d+\.\d+)", out.stdout + out.stderr)
        return m.group(1) if m else None
    except (OSError, subprocess.SubprocessError):
        return None


def _prepare_notebook(src: Path, dest: Path, *, hide_code: bool) -> None:
    """Copy the executed notebook into the build; optionally collapse code cells (MyST `hide-input` tag)."""
    nb = nbformat.read(src, as_version=4)
    if hide_code:
        for cell in nb.cells:
            if cell.cell_type == "code":
                tags = set(cell.metadata.get("tags", []))
                if not tags & {"remove-input", "remove-cell", "show-input"}:
                    tags.add("hide-input")
                cell.metadata["tags"] = sorted(tags)
    nbformat.write(nb, dest)


def render(run: NotebookRun, ctx: RunContext, base_url: str) -> Path:
    """
    Build `ctx.executed_path` into `ctx.build_dir/_build/html`.

    `base_url` is the URL path (or absolute URL path) the site will be served
    under, so MyST rewrites asset links accordingly.
    """
    cfg = settings.NOTEBOOK_FACTORY
    build = ctx.build_dir
    if build.exists():
        shutil.rmtree(build)
    build.mkdir(parents=True)
    _prepare_notebook(ctx.executed_path, build / "notebook.ipynb", hide_code=cfg["MYST_HIDE_CODE"])
    # anywidget outputs (lonboard, manywidgets, ...) become kernel-free static widgets via
    # developmentseed/myst-anywidget-static-export; the released plugin is vendored next to this module.
    plugin = Path(__file__).parent / "myst_plugins" / "anywidget-static-export.mjs"
    shutil.copy(plugin, build / "anywidget-static-export.mjs")
    # Ship any images/data the notebook wrote next to it, so relative links keep working.
    for item in ctx.outputs_dir.iterdir():
        target = build / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy(item, target)

    project = {
        "version": 1,
        "project": {
            "title": run.title,
            "description": run.notebook.summary or run.notebook.title,
            "keywords": [k for k in [run.notebook.use_case, run.hazard.key if run.hazard_id else None] if k],
            "plugins": ["anywidget-static-export.mjs"],
            "toc": [{"file": "notebook.ipynb"}],
        },
        "site": {
            "template": cfg["MYST_TEMPLATE"],
            "title": settings.SITE_NAME,
            "options": {
                "hide_toc": True,
                "hide_footer_links": True,
                "folders": False,
            },
        },
    }
    (build / "myst.yml").write_text(yaml.safe_dump(project, sort_keys=False))

    # Share the downloaded theme between runs instead of re-fetching it every time.
    cache = Path(cfg["MYST_TEMPLATE_CACHE_DIR"])
    cache.mkdir(parents=True, exist_ok=True)
    (build / "_build").mkdir(exist_ok=True)
    os.symlink(cache.resolve(), build / "_build" / "templates", target_is_directory=True)

    env = {
        **os.environ,
        "BASE_URL": base_url.rstrip("/"),
        "CI": "true",
        "MYST_TELEMETRY": "0",
        # MyST fetches its own build server on "localhost"; make Node resolve that to IPv4 in containers.
        "NODE_OPTIONS": os.environ.get("NODE_OPTIONS", "--dns-result-order=ipv4first"),
    }
    cmd = [*_myst_cmd(), "build", "--html"]
    try:
        proc = subprocess.run(
            cmd, cwd=build, env=env, capture_output=True, text=True, timeout=cfg["MYST_BUILD_TIMEOUT"]
        )
    except FileNotFoundError as exc:
        raise RenderError(
            f"MyST CLI not found ({cmd[0]}). Install with `npm i -g mystmd` or set MYST_COMMAND."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RenderError("MyST build timed out") from exc
    tail = "\n".join((proc.stdout + "\n" + proc.stderr).strip().splitlines()[-15:])
    html = build / "_build" / "html"
    if proc.returncode != 0 or not (html / "index.html").exists():
        raise RenderError(f"MyST build failed (exit {proc.returncode}):\n{tail}")
    built = next((ln.strip() for ln in reversed(tail.splitlines()) if "Built" in ln), "build complete")
    run.log(f"myst: {built}")

    if cfg["MYST_STRIP_THEBE"]:
        for f in html.glob("*thebe*"):
            f.unlink()
    ctx.html_dir = html
    return html
