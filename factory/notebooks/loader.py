"""
Import notebook templates from a templates directory (notebook_templates/ by default).

Each template lives in its own folder:

    notebook_templates/<slug>/
        template.yml     metadata + parameter schema
        notebook.ipynb   the template (with a cell tagged `parameters`)
        ...              helper modules or small data files

template.yml keys: title, summary, description, filters ({facet key: [option keys]},
e.g. {hazard: [flood], use_case: [uc1]}; the older `hazards:` and `use_case:` keys
are read too), notebook (file name, default notebook.ipynb), version, requires_area, area_levels,
requires_stac_item (alias: requires_event), stac_hints ({profile, collections, roles}; soft
filters for the run-form pickers), supports_writeback, estimated_runtime_minutes,
parameters (JSON Schema).

Templates are managed in the app (Templates → New template) and versioned; this
importer is for seeding and for templates kept in git. A folder becomes a new
template (bundle v1), or a new version of an existing one when its files or
versioned settings changed — but never over a version made in the app unless
forced, and title / summary / filters are only set on creation unless asked.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml
from django.conf import settings
from django.db import transaction

from . import bundles
from . import templates as T
from .models import AnalysisNotebook, SourceKind

log = logging.getLogger(__name__)


@dataclass
class SyncResult:
    slug: str
    action: str  # created | new version | converted | updated (local) | unchanged | skipped
    notebook: AnalysisNotebook | None = None
    detail: str = ""


def discover(templates_dir: Path | None = None) -> list[tuple[Path, dict]]:
    root = Path(templates_dir or settings.NOTEBOOK_FACTORY["TEMPLATES_DIR"])
    found = []
    if not root.exists():
        return found
    for meta_path in sorted(root.glob("*/template.yml")):
        with meta_path.open() as fh:
            meta = yaml.safe_load(fh) or {}
        found.append((meta_path.parent, meta))
    return found


def _update_local(nb: AnalysisNotebook, root: Path, folder: Path, display: dict, snapshot: dict) -> None:
    """Templates that are still served from the templates dir are updated in place, as before versioning."""
    rel = (folder / snapshot["notebook_path"]).relative_to(root).as_posix()
    for key, value in {**display, **snapshot, "notebook_path": rel}.items():
        setattr(nb, "version" if key == "label" else key, value)
    nb.save()


def _sync_one(folder: Path, meta: dict, root: Path, opts: dict) -> SyncResult:
    slug = meta.get("slug") or folder.name
    display, snapshot = T.meta_from_yaml(meta, slug)
    if not (folder / snapshot["notebook_path"]).is_file():
        return SyncResult(slug, "skipped", detail=f"{snapshot['notebook_path']} not found")
    nb = AnalysisNotebook.objects.filter(slug=slug).select_related("current_version").first()
    if nb is None:
        if opts["dry_run"]:
            return SyncResult(slug, "created")
        bundle = bundles.zip_folder(folder)
        nb = T.create_template(
            slug,
            display,
            snapshot,
            bundle=bundle,
            options=T.options_from_yaml(meta, slug),
            note="sync_templates",
        )
        return SyncResult(slug, "created", nb)
    if opts["only_missing"]:
        return SyncResult(slug, "unchanged", nb, "exists")
    if nb.source_kind == SourceKind.GIT:
        return SyncResult(slug, "skipped", nb, "git template")

    if nb.source_kind == SourceKind.LOCAL and not opts["convert"]:
        if not opts["dry_run"]:
            _update_local(nb, root, folder, display, snapshot)
            nb.facet_options.set(T.options_from_yaml(meta, slug))
        return SyncResult(slug, "updated (local)", nb)

    bundle = bundles.zip_folder(folder)
    current = nb.current_version
    changed = nb.source_kind == SourceKind.LOCAL or current is None or current.sha256 != bundle.sha256
    changed = changed or T.snapshot_differs(nb, snapshot)
    action = "converted" if nb.source_kind == SourceKind.LOCAL else "new version"
    if changed and current is not None and current.created_by_id and not opts["force"]:
        return SyncResult(slug, "skipped", nb, f"v{current.number} was made in the app (use --force)")
    if opts["dry_run"]:
        return SyncResult(slug, action if changed else "unchanged", nb)
    if changed:
        T.add_version(nb, snapshot, bundle=bundle, note="sync_templates")
    if opts["overwrite_metadata"]:
        for key, value in display.items():
            setattr(nb, key, value)
        nb.save(update_fields=[*display, "updated_at"])
        nb.facet_options.set(T.options_from_yaml(meta, slug))
    return SyncResult(slug, action if changed else "unchanged", nb)


def sync_templates(
    templates_dir: Path | None = None,
    deactivate_missing: bool = False,
    *,
    only_missing: bool = False,
    convert: bool = False,
    force: bool = False,
    overwrite_metadata: bool = False,
    dry_run: bool = False,
) -> list[SyncResult]:
    root = Path(templates_dir or settings.NOTEBOOK_FACTORY["TEMPLATES_DIR"])
    opts = {
        "only_missing": only_missing,
        "convert": convert,
        "force": force,
        "overwrite_metadata": overwrite_metadata,
        "dry_run": dry_run,
    }
    results = []
    for folder, meta in discover(root):
        with transaction.atomic():
            result = _sync_one(folder, meta, root, opts)
        log.info("template %s: %s %s", result.slug, result.action, result.detail)
        results.append(result)
    if deactivate_missing and not dry_run:
        seen = {r.slug for r in results}
        AnalysisNotebook.objects.filter(source_kind=SourceKind.LOCAL).exclude(slug__in=seen).update(
            is_active=False
        )
    return results
