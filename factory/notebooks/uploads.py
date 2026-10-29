"""
Uploaded notebooks: already executed .ipynb files, zipped together with the files
they link to, that are rendered and published like runs without executing anything.

An upload is a `NotebookRun` without a template (`notebook=None`, trigger "upload").
The original archive is kept in the published storage as `<run prefix>/source.zip`:
the web process writes it, the worker unpacks it (they share no disk), and a retry
re-renders from it.
"""

from __future__ import annotations

import io
import logging
import shutil
import stat
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import nbformat
from django.conf import settings
from django.core.files.base import File
from django.db import transaction

from factory.catalog import facets

from .models import NotebookRun, TriggerKind
from .pipeline import publisher
from .pipeline.context import RunContext

log = logging.getLogger(__name__)

IGNORED_PARTS = {"__MACOSX", ".ipynb_checkpoints", "__pycache__", ".git"}


class ArchiveError(ValueError):
    """A zip or notebook that cannot be accepted (uploads and template bundles)."""


UploadError = ArchiveError


@dataclass
class UploadManifest:
    """What an archive holds. `notebook` is set once the notebook to publish is known."""

    notebooks: list[str]
    notebook: str | None = None
    title: str = ""
    warnings: list[str] = field(default_factory=list)


# -- archives ------------------------------------------------------------------
def _size(fileobj) -> int:
    size = getattr(fileobj, "size", None)
    if size is None:
        pos = fileobj.tell()
        size = fileobj.seek(0, io.SEEK_END)
        fileobj.seek(pos)
    return size


def as_archive(fileobj, filename: str = "", max_bytes: int | None = None):
    """The upload as a zip file object; a bare .ipynb is wrapped in a one-file zip."""
    max_bytes = max_bytes or settings.NOTEBOOK_FACTORY["UPLOAD_MAX_BYTES"]
    if _size(fileobj) > max_bytes:
        raise UploadError(f"The upload is larger than {max_bytes // (1024 * 1024)} MB.")
    fileobj.seek(0)
    if zipfile.is_zipfile(fileobj):
        fileobj.seek(0)
        return fileobj
    fileobj.seek(0)
    name = PurePosixPath(filename or getattr(fileobj, "name", "") or "notebook.ipynb").name
    if not name.endswith(".ipynb"):
        raise UploadError("Upload a .zip archive or a .ipynb notebook.")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(name, fileobj.read())
    buf.seek(0)
    return buf


def ignored(name: str) -> bool:
    return bool(set(PurePosixPath(name).parts) & IGNORED_PARTS)


def safe_members(zf: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    """The archive's files, after refusing unsafe paths, symlinks and zip bombs."""
    cfg = settings.NOTEBOOK_FACTORY
    infos = [i for i in zf.infolist() if not i.is_dir()]
    if len(infos) > cfg["UPLOAD_MAX_FILES"]:
        raise UploadError(f"The archive holds more than {cfg['UPLOAD_MAX_FILES']} files.")
    if sum(i.file_size for i in infos) > cfg["UPLOAD_MAX_UNCOMPRESSED_BYTES"]:
        raise UploadError(
            f"The archive unpacks to more than {cfg['UPLOAD_MAX_UNCOMPRESSED_BYTES'] // (1024 * 1024)} MB."
        )
    out = []
    for info in infos:
        path = PurePosixPath(info.filename.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts or ":" in path.parts[0]:
            raise UploadError(f"Unsafe path in archive: {info.filename}")
        if stat.S_ISLNK(info.external_attr >> 16):
            raise UploadError(f"Symbolic links are not allowed: {info.filename}")
        if not ignored(info.filename):
            out.append(info)
    return out


def open_zip(archive) -> zipfile.ZipFile:
    archive.seek(0)
    try:
        return zipfile.ZipFile(archive)
    except zipfile.BadZipFile as exc:
        raise UploadError("The archive is not a valid zip file.") from exc


# -- notebooks -----------------------------------------------------------------
def read_notebook(raw: bytes, name: str):
    try:
        nb = nbformat.reads(raw.decode("utf-8"), as_version=4)
        nbformat.validate(nb)
    except (UnicodeDecodeError, ValueError, nbformat.ValidationError) as exc:
        raise UploadError(f"{name} is not a valid notebook: {exc}") from exc
    return nb


def notebook_title(nb) -> str:
    """The first markdown `# heading`, if any."""
    for cell in nb.cells:
        if cell.cell_type != "markdown":
            continue
        for line in cell.source.splitlines():
            if line.startswith("# "):
                return line[2:].strip()[:200]
    return ""


def execution_warnings(nb) -> list[str]:
    code = [c for c in nb.cells if c.cell_type == "code" and c.source.strip()]
    unexecuted = [c for c in code if c.get("execution_count") is None and not c.get("outputs")]
    if code and len(unexecuted) == len(code):
        return ["The notebook has not been executed: no code cell has a result."]
    if unexecuted:
        return [f"{len(unexecuted)} of {len(code)} code cells have not been executed."]
    return []


def inspect_archive(archive, notebook_path: str = "") -> UploadManifest:
    """Validate an archive without unpacking it, and describe the notebook to publish."""
    with open_zip(archive) as zf:
        members = safe_members(zf)
        notebooks = sorted(
            (i.filename for i in members if i.filename.endswith(".ipynb")),
            key=lambda n: (n.count("/"), n),
        )
        if not notebooks:
            raise UploadError("The archive holds no .ipynb notebook.")
        if notebook_path and notebook_path not in notebooks:
            raise UploadError(f"{notebook_path} is not in the archive.")
        manifest = UploadManifest(notebooks=notebooks)
        chosen = notebook_path or (notebooks[0] if len(notebooks) == 1 else None)
        if chosen:
            nb = read_notebook(zf.read(chosen), chosen)
            manifest.notebook = chosen
            manifest.title = notebook_title(nb)
            manifest.warnings = execution_warnings(nb)
    archive.seek(0)
    return manifest


# -- runs ----------------------------------------------------------------------
def source_key(run: NotebookRun) -> str:
    return f"{publisher.run_prefix(run)}/source.zip"


def create_upload(
    fileobj,
    *,
    filename: str = "",
    notebook_path: str = "",
    title: str = "",
    summary: str = "",
    description: str = "",
    area=None,
    stac_item=None,
    filters: dict[str, list[str]] | None = None,
    event_refs: list[str] | None = None,
    user=None,
    enqueue: bool = True,
) -> NotebookRun:
    """Store an uploaded notebook archive as a new run and queue it for rendering."""
    from .services import enqueue_run

    archive = as_archive(fileobj, filename)
    manifest = inspect_archive(archive, notebook_path)
    if manifest.notebook is None:
        raise UploadError("The archive holds several notebooks; pick the one to publish.")
    fallback = PurePosixPath(manifest.notebook).stem.replace("_", " ").replace("-", " ")
    emails = [user.email] if getattr(user, "email", "") else []

    with transaction.atomic():
        run = NotebookRun.objects.create(
            notebook=None,
            trigger=TriggerKind.UPLOAD,
            display_title=(title or manifest.title or fallback)[:200],
            summary=summary,
            description=description,
            upload_path=manifest.notebook,
            area=area,
            stac_item=stac_item,
            event_refs=list(event_refs or []),
            triggered_by=user if getattr(user, "is_authenticated", False) else None,
            notify_emails=emails,
        )
        facets.set_run_facets(run, facets.resolve_run(None, {}, stac_item, filters))
        publisher.published_storage().save(source_key(run), File(archive, name="source.zip"))
        run.log(f"uploaded {manifest.notebook} and queued for rendering")
        for warning in manifest.warnings:
            run.log(warning, level="warning")
        if enqueue:
            transaction.on_commit(lambda: enqueue_run(run))
    return run


def retry_upload(run: NotebookRun, user=None) -> NotebookRun:
    """A fresh upload run from the same archive and metadata (e.g. to re-render after a theme upgrade)."""
    storage = publisher.published_storage()
    with storage.open(source_key(run), "rb") as fh:
        archive = io.BytesIO(fh.read())
    return create_upload(
        archive,
        notebook_path=run.upload_path,
        title=run.display_title,
        summary=run.summary,
        description=run.description,
        area=run.area,
        stac_item=run.stac_item,
        filters=facets.manual_tags(run),
        event_refs=run.event_refs,
        user=user or run.triggered_by,
    )


def unpack(run: NotebookRun, ctx: RunContext) -> Path:
    """Fetch and unpack the archive; the notebook's sibling files go to outputs/ so they ship with the page."""
    local = ctx.workdir / "source.zip"
    with publisher.published_storage().open(source_key(run), "rb") as src, local.open("wb") as dst:
        shutil.copyfileobj(src, dst)
    dest = ctx.workdir / "upload"
    with local.open("rb") as fh, open_zip(fh) as zf:
        for info in safe_members(zf):
            zf.extract(info, dest)
    nb_path = dest / run.upload_path
    if not nb_path.is_file():
        raise UploadError(f"{run.upload_path} is missing from the archive.")
    read_notebook(nb_path.read_bytes(), run.upload_path)
    for item in nb_path.parent.iterdir():
        if item == nb_path:
            continue
        target = ctx.outputs_dir / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy(item, target)
    ctx.executed_path = nb_path
    return nb_path
