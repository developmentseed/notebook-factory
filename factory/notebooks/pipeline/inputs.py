"""
Prepare the inputs a notebook receives.

Reserved parameters injected into every run (templates should declare these in
their parameters cell with harmless defaults so they also run standalone):

  run_id                 str   the run UUID
  inputs_dir             str   folder with prepared inputs
  output_dir             str   folder the notebook may write artifacts to (published alongside the HTML)
  area                   dict  {id, code, name, level, country, country_name} or {}
  area_geojson           str   path to a FeatureCollection with the target area (or "")
  subdivisions_geojson   str   path to a FeatureCollection with the aggregation subdivisions (or "")
  stac_item              dict  the selected STAC item (or {})
  stac_item_json         str   path to the STAC item as JSON (or "")
  stac_collection        str   the item's collection id (or "")
  stac_api_url           str   STAC API root of the item's catalog (or "")
  stac_catalog_profile   str   "generic" | "montandon" (or "")
  hazard                 str   hazard key (or "")

The catalog's API key is passed through the environment (STAC_API_KEY, next to
STAC_API_URL), never as a parameter, so it does not end up inside the published
notebook.
"""

from __future__ import annotations

import json
from typing import Any

from ..models import NotebookRun
from .context import RunContext


def _write_json(path, payload) -> str:
    path.write_text(json.dumps(payload, indent=1, default=str))
    return str(path)


def prepare_inputs(run: NotebookRun, ctx: RunContext) -> dict[str, Any]:
    injected: dict[str, Any] = {
        "run_id": str(run.id),
        "inputs_dir": str(ctx.inputs_dir),
        "output_dir": str(ctx.outputs_dir),
        "area": {},
        "area_geojson": "",
        "subdivisions_geojson": "",
        "stac_item": {},
        "stac_item_json": "",
        "stac_collection": "",
        "stac_api_url": "",
        "stac_catalog_profile": "",
        "hazard": run.hazard.key if run.hazard_id else "",
    }
    if run.area_id:
        area = run.area
        injected["area"] = {
            "id": area.pk,
            "code": area.code,
            "name": area.name,
            "level": area.level,
            "country": area.country_id,
            "country_name": area.country.name,
            "bbox": area.bbox,
        }
        injected["area_geojson"] = _write_json(
            ctx.inputs_dir / "area.geojson",
            {"type": "FeatureCollection", "features": [area.as_feature()]},
        )
        # Subdivisions: honour an `aggregation_level` parameter if present, else children.
        agg_level = run.parameters.get("aggregation_level")
        try:
            agg_level = int(agg_level) if agg_level not in (None, "") else None
        except (TypeError, ValueError):
            agg_level = None
        if agg_level is None or agg_level <= area.level:
            subdivisions = (
                area.children.all() if area.children.exists() else area.__class__.objects.filter(pk=area.pk)
            )
        else:
            subdivisions = area.descendants_at_level(agg_level)
            if not subdivisions.exists():
                subdivisions = area.children.all() or area.__class__.objects.filter(pk=area.pk)
        injected["subdivisions_geojson"] = _write_json(
            ctx.inputs_dir / "subdivisions.geojson",
            {"type": "FeatureCollection", "features": [a.as_feature() for a in subdivisions]},
        )
    if run.stac_item_id:
        item = run.stac_item
        injected["stac_item"] = item.raw or item.as_stac_item()
        injected["stac_item_json"] = _write_json(ctx.inputs_dir / "stac_item.json", injected["stac_item"])
        injected["stac_collection"] = item.collection
        injected["stac_api_url"] = item.catalog.base_url
        injected["stac_catalog_profile"] = item.catalog.profile
    ctx.injected = injected
    return injected
