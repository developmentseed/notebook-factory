"""Locate the template notebook for a run: local templates dir or a git checkout."""

from __future__ import annotations

import hashlib
import logging
import shutil
import subprocess
from pathlib import Path

from django.conf import settings

from ..models import AnalysisNotebook, SourceKind
from .context import RunContext

log = logging.getLogger(__name__)


class SourceError(RuntimeError):
    pass


def resolve_template(notebook: AnalysisNotebook, ctx: RunContext) -> Path:
    """Copy the template notebook (and its sibling files) into the work dir and return its path."""
    if notebook.source_kind == SourceKind.GIT:
        root = _git_checkout(notebook)
        src = root / notebook.notebook_path
    else:
        root = Path(settings.NOTEBOOK_FACTORY["TEMPLATES_DIR"])
        src = root / notebook.notebook_path
    if not src.exists():
        raise SourceError(f"Template notebook not found: {src}")
    # Copy the notebook's folder so templates can ship helper modules/data next to the .ipynb.
    dest_dir = ctx.workdir / "template"
    if dest_dir.exists():
        shutil.rmtree(dest_dir)
    shutil.copytree(
        src.parent,
        dest_dir,
        ignore=shutil.ignore_patterns(".git", "_build", "__pycache__", ".ipynb_checkpoints"),
    )
    ctx.template_path = dest_dir / src.name
    return ctx.template_path


def _git_checkout(notebook: AnalysisNotebook) -> Path:
    if not notebook.source_repo:
        raise SourceError("source_repo is empty")
    cache = Path(settings.NOTEBOOK_FACTORY["GIT_CACHE_DIR"])
    cache.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(f"{notebook.source_repo}@{notebook.source_ref}".encode()).hexdigest()[:12]
    dest = cache / f"{notebook.slug}-{key}"
    ref = notebook.source_ref or "HEAD"
    try:
        if not dest.exists():
            cmd = ["git", "clone", "--depth", "1"]
            if notebook.source_ref:
                cmd += ["--branch", notebook.source_ref]
            cmd += [notebook.source_repo, str(dest)]
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=300)
        else:
            subprocess.run(
                ["git", "-C", str(dest), "fetch", "--depth", "1", "origin", ref],
                check=True,
                capture_output=True,
                text=True,
                timeout=300,
            )
            subprocess.run(
                ["git", "-C", str(dest), "checkout", "-q", "FETCH_HEAD"],
                check=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
    except subprocess.CalledProcessError as exc:
        raise SourceError(f"git failed: {exc.stderr.strip()}") from exc
    return dest
