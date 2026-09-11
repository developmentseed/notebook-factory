"""
Load notebook templates from the local templates directory into the database.

Each template lives in its own folder:

    notebook_templates/<slug>/
        template.yml     metadata + parameter schema
        notebook.ipynb   the template (with a cell tagged `parameters`)
        ...              helper modules or small data files

template.yml keys: title, summary, description, use_case, hazards, notebook (file
name, default notebook.ipynb), version, requires_area, area_levels,
requires_event, supports_writeback, estimated_runtime_minutes, parameters (JSON Schema).
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml
from django.conf import settings
from django.db import transaction

from factory.catalog.models import Hazard

from .models import AnalysisNotebook, SourceKind

log = logging.getLogger(__name__)


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


@transaction.atomic
def sync_templates(
    templates_dir: Path | None = None, deactivate_missing: bool = False
) -> list[AnalysisNotebook]:
    root = Path(templates_dir or settings.NOTEBOOK_FACTORY["TEMPLATES_DIR"])
    synced = []
    seen = set()
    for folder, meta in discover(root):
        slug = meta.get("slug") or folder.name
        nb_file = meta.get("notebook", "notebook.ipynb")
        rel = (folder / nb_file).relative_to(root).as_posix()
        defaults = {
            "title": meta.get("title", slug),
            "summary": meta.get("summary", ""),
            "description": meta.get("description", ""),
            "use_case": meta.get("use_case", "other"),
            "source_kind": SourceKind.LOCAL,
            "source_repo": meta.get("source_repo", ""),
            "source_ref": meta.get("source_ref", ""),
            "notebook_path": rel,
            "version": str(meta.get("version", "")),
            "parameter_schema": meta.get("parameters", {}) or {},
            "requires_area": bool(meta.get("requires_area", True)),
            "area_levels": meta.get("area_levels", []) or [],
            "requires_event": bool(meta.get("requires_event", False)),
            "supports_writeback": bool(meta.get("supports_writeback", False)),
            "estimated_runtime_minutes": meta.get("estimated_runtime_minutes"),
            "is_active": bool(meta.get("active", True)),
        }
        nb, created = AnalysisNotebook.objects.update_or_create(slug=slug, defaults=defaults)
        hazards = Hazard.objects.filter(key__in=meta.get("hazards", []) or [])
        nb.hazards.set(hazards)
        synced.append(nb)
        seen.add(slug)
        log.info("%s template %s", "Created" if created else "Updated", slug)
    if deactivate_missing:
        AnalysisNotebook.objects.filter(source_kind=SourceKind.LOCAL).exclude(slug__in=seen).update(
            is_active=False
        )
    return synced
