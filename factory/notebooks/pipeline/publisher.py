"""Upload the rendered site (and the executed notebook) to the published storage."""

from __future__ import annotations

import logging
import mimetypes
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from django.conf import settings
from django.core.files.base import File
from django.core.files.storage import storages

from ..models import NotebookRun
from .context import RunContext

log = logging.getLogger(__name__)


def published_storage():
    return storages["published"]


def run_prefix(run: NotebookRun) -> str:
    return f"{settings.NOTEBOOK_FACTORY['PUBLISH_PREFIX'].strip('/')}/{run.id}"


def public_url(key: str) -> str:
    """Public URL of a published file. Local storage yields a host-relative URL (served by Django)."""
    base = settings.PUBLISHED_PUBLIC_BASE_URL
    if base:
        return f"{base.rstrip('/')}/{key.lstrip('/')}"
    return published_storage().url(key)


def absolute_url(url: str) -> str:
    if url.startswith("/"):
        return settings.PUBLIC_BASE_URL.rstrip("/") + url
    return url


def base_url_path(run: NotebookRun) -> str:
    """The path component MyST should use as BASE_URL for this run."""
    from urllib.parse import urlparse

    url = public_url(f"{run_prefix(run)}/index.html")
    path = urlparse(url).path
    return path.rsplit("/index.html", 1)[0]


def _upload_file(storage, key: str, path: Path) -> None:
    content_type = mimetypes.guess_type(str(path))[0]
    with path.open("rb") as fh:
        f = File(fh, name=path.name)
        if content_type:
            f.content_type = content_type
        # Backends that overwrite in place (S3 file_overwrite, Azure overwrite_files) need no
        # exists/delete round trips; the local FileSystemStorage does.
        if not getattr(
            storage, "file_overwrite", getattr(storage, "overwrite_files", False)
        ) and storage.exists(key):
            storage.delete(key)
        storage.save(key, f)


def _upload_many(storage, items: list[tuple[str, Path]]) -> int:
    workers = settings.NOTEBOOK_FACTORY.get("UPLOAD_WORKERS", 8)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(lambda kp: _upload_file(storage, *kp), items))
    return len(items)


def publish(run: NotebookRun, ctx: RunContext) -> dict:
    storage = published_storage()
    prefix = run_prefix(run)
    html = ctx.html_dir
    assert html is not None and ctx.executed_path is not None

    items = [(f"{prefix}/{p.relative_to(html).as_posix()}", p) for p in html.rglob("*") if p.is_file()]
    items.append((f"{prefix}/notebook.ipynb", ctx.executed_path))
    artifacts = {}
    for path in ctx.outputs_dir.rglob("*"):
        if path.is_file():
            rel = path.relative_to(ctx.outputs_dir).as_posix()
            key = f"{prefix}/outputs/{rel}"
            items.append((key, path))
            artifacts[rel] = public_url(key)
    count = _upload_many(storage, items)

    run.log(f"published {count} files to {prefix}/")
    return {
        "output_prefix": prefix,
        "output_url": public_url(f"{prefix}/index.html"),
        "notebook_url": public_url(f"{prefix}/notebook.ipynb"),
        "artifacts": artifacts,
    }
