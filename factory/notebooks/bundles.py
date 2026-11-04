"""
Template bundles: a template's notebook and the files next to it (helper modules,
small data), zipped and kept in the published storage as
`templates/<slug>/v<n>-<sha>.zip`, because web and worker share no disk.

Bundles are normalised (sorted entries, fixed timestamps, ignored files dropped,
`template.yml` left out since the database owns the metadata) and hashed by
content, so the same files always give the same sha256. Workers unpack each
bundle once into TEMPLATE_CACHE_DIR/<sha256>/.
"""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import yaml
from django.conf import settings

from .uploads import ArchiveError, as_archive, ignored, notebook_title, open_zip, read_notebook, safe_members

PREFIX = "templates"
META_FILE = "template.yml"
FIXED_TIME = (1980, 1, 1, 0, 0, 0)
# Left out when zipping a templates folder: build output and what a local run writes.
FOLDER_IGNORED = {"_build", "outputs"}


@dataclass
class Bundle:
    data: bytes
    sha256: str
    files: list[str]


@dataclass
class TemplateManifest:
    """What an uploaded template archive holds. `notebook` is set once the notebook to run is known."""

    notebooks: list[str]
    notebook: str | None = None
    title: str = ""
    meta: dict = field(default_factory=dict)
    nb: object = None
    bundle: Bundle | None = None
    warnings: list[str] = field(default_factory=list)


def storage():
    from django.core.files.storage import storages

    return storages["published"]


def bundle_key(slug: str, number: int, sha256: str) -> str:
    return f"{PREFIX}/{slug}/v{number}-{sha256[:12]}.zip"


def _hash(entries: list[tuple[str, bytes]]) -> str:
    h = hashlib.sha256()
    for name, data in entries:
        h.update(name.encode() + b"\0" + hashlib.sha256(data).digest())
    return h.hexdigest()


def pack(entries: list[tuple[str, bytes]]) -> Bundle:
    entries = sorted(entries)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries:
            info = zipfile.ZipInfo(name, FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, data)
    return Bundle(buf.getvalue(), _hash(entries), [n for n, _ in entries])


def _strip_meta(entries, notebook_path: str):
    meta_path = (PurePosixPath(notebook_path).parent / META_FILE).as_posix().removeprefix("./")
    return [(n, d) for n, d in entries if n != meta_path]


def zip_folder(folder: Path) -> Bundle:
    """A templates folder (notebook_templates/<slug>/) as a bundle; template.yml is left out."""
    entries = []
    for path in sorted(folder.rglob("*")):
        rel = path.relative_to(folder).as_posix()
        if path.is_symlink() or not path.is_file() or ignored(rel) or rel.split("/")[0] in FOLDER_IGNORED:
            continue
        if rel == META_FILE:
            continue
        entries.append((rel, path.read_bytes()))
    return pack(entries)


def read_entries(archive) -> list[tuple[str, bytes]]:
    with open_zip(archive) as zf:
        return [(i.filename, zf.read(i)) for i in safe_members(zf)]


def _choose(notebooks: list[str], metas: dict[str, dict], notebook_path: str) -> str | None:
    if notebook_path:
        if notebook_path not in notebooks:
            raise ArchiveError(f"{notebook_path} is not in the archive.")
        return notebook_path
    if len(notebooks) == 1:
        return notebooks[0]
    # A template.yml names its notebook (default notebook.ipynb next to it).
    named = [
        (PurePosixPath(folder) / meta.get("notebook", "notebook.ipynb")).as_posix().removeprefix("./")
        for folder, meta in metas.items()
    ]
    named = [n for n in named if n in notebooks]
    return named[0] if len(named) == 1 else None


def inspect_template(fileobj, filename: str = "", notebook_path: str = "") -> TemplateManifest:
    """Validate an uploaded template (zip or .ipynb) and describe it; nothing is stored."""
    from . import schema_builder

    archive = as_archive(fileobj, filename, max_bytes=settings.NOTEBOOK_FACTORY["TEMPLATE_MAX_BYTES"])
    entries = read_entries(archive)
    notebooks = sorted((n for n, _ in entries if n.endswith(".ipynb")), key=lambda n: (n.count("/"), n))
    if not notebooks:
        raise ArchiveError("The archive holds no .ipynb notebook.")
    metas = {}
    for name, data in entries:
        if PurePosixPath(name).name == META_FILE:
            try:
                metas[PurePosixPath(name).parent.as_posix()] = yaml.safe_load(data) or {}
            except yaml.YAMLError as exc:
                raise ArchiveError(f"{name} is not valid YAML: {exc}") from exc
    manifest = TemplateManifest(notebooks=notebooks)
    chosen = _choose(notebooks, metas, notebook_path)
    if chosen is None:
        return manifest
    nb = read_notebook(dict(entries)[chosen], chosen)
    manifest.notebook = chosen
    manifest.nb = nb
    manifest.title = notebook_title(nb)
    manifest.meta = metas.get(PurePosixPath(chosen).parent.as_posix(), {})
    manifest.bundle = pack(_strip_meta(entries, chosen))
    manifest.warnings = schema_builder.notebook_warnings(
        nb, manifest.bundle.files, chosen, secret_names=manifest.meta.get("secrets") or ()
    )
    return manifest


def save(bundle: Bundle, key: str) -> None:
    from django.core.files.base import ContentFile

    storage().save(key, ContentFile(bundle.data, name=PurePosixPath(key).name))


def load(key: str) -> bytes:
    with storage().open(key, "rb") as fh:
        return fh.read()


def materialise(version) -> Path:
    """The version's bundle unpacked on this machine (cached by content hash)."""
    cache = Path(settings.NOTEBOOK_FACTORY["TEMPLATE_CACHE_DIR"])
    dest = cache / version.sha256
    if dest.is_dir():
        os.utime(dest)
        return dest
    cache.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(dir=cache, prefix=".tmp-"))
    try:
        entries = read_entries(io.BytesIO(load(version.archive_key)))
        if _hash(sorted(entries)) != version.sha256:
            raise ArchiveError(f"Bundle {version.archive_key} does not match its recorded hash.")
        for name, data in entries:
            target = tmp / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        try:
            tmp.rename(dest)
        except OSError:
            if not dest.is_dir():  # another worker unpacking the same bundle got there first
                raise
    finally:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
    return dest


def prune_cache(max_age_hours: float) -> int:
    cache = Path(settings.NOTEBOOK_FACTORY["TEMPLATE_CACHE_DIR"])
    if not cache.exists():
        return 0
    cutoff = time.time() - max_age_hours * 3600
    removed = 0
    for path in cache.iterdir():
        if path.is_dir() and path.stat().st_mtime < cutoff:
            shutil.rmtree(path, ignore_errors=True)
            removed += 1
    return removed
