"""
Load notebook templates from the local templates directory into the database.

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
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml
from django.conf import settings
from django.db import transaction

from factory.catalog.facets import options_by_keys
from factory.catalog.models import Facet

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


def _template_options(slug: str, meta: dict) -> list:
    """Facet options named in template.yml; unknown filters / options are logged and skipped."""
    wanted = {k: v for k, v in (meta.get("filters") or {}).items()}
    if meta.get("hazards") and "hazard" not in wanted:
        wanted["hazard"] = meta["hazards"]
    if meta.get("use_case") and "use_case" not in wanted:
        wanted["use_case"] = [meta["use_case"]]
    options = []
    facets = {f.key: f for f in Facet.objects.filter(key__in=list(wanted))}
    for key, values in wanted.items():
        if key not in facets:
            log.warning("template %s: unknown filter %r ignored", slug, key)
            continue
        options.extend(options_by_keys(facets[key], values if isinstance(values, list) else [values]))
    return options


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
            "source_kind": SourceKind.LOCAL,
            "source_repo": meta.get("source_repo", ""),
            "source_ref": meta.get("source_ref", ""),
            "notebook_path": rel,
            "version": str(meta.get("version", "")),
            "parameter_schema": meta.get("parameters", {}) or {},
            "requires_area": bool(meta.get("requires_area", True)),
            "area_levels": meta.get("area_levels", []) or [],
            "requires_stac_item": bool(meta.get("requires_stac_item", meta.get("requires_event", False))),
            "stac_hints": meta.get("stac_hints", {}) or {},
            "supports_writeback": bool(meta.get("supports_writeback", False)),
            "estimated_runtime_minutes": meta.get("estimated_runtime_minutes"),
            "is_active": bool(meta.get("active", True)),
        }
        nb, created = AnalysisNotebook.objects.update_or_create(slug=slug, defaults=defaults)
        nb.facet_options.set(_template_options(slug, meta))
        synced.append(nb)
        seen.add(slug)
        log.info("%s template %s", "Created" if created else "Updated", slug)
    if deactivate_missing:
        AnalysisNotebook.objects.filter(source_kind=SourceKind.LOCAL).exclude(slug__in=seen).update(
            is_active=False
        )
    return synced
