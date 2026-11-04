"""
Catalog profiles: how the derived StacItem fields are read from a raw STAC item.

`generic` uses only core STAC properties. `montandon` also reads the monty:*
extension (correlation id, ISO3 country codes), which is what country filters
on trigger rules match against, and the source ids that disaster search uses. Filter options (e.g. hazards) are not part of
the profile: any catalog's items are tagged via each filter's `stac_property`
(see factory.catalog.facets.for_stac_item).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from django.contrib.gis.geos import GEOSGeometry
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from . import refs
from .models import CatalogProfile


def _dt(value: Any) -> datetime | None:
    if not value:
        return None
    dt = parse_datetime(str(value))
    if dt and dt.tzinfo is None:
        dt = timezone.make_aware(dt)
    return dt


def _geom(item: dict):
    if not item.get("geometry"):
        return None
    try:
        return GEOSGeometry(json.dumps(item["geometry"]), srid=4326)
    except Exception:  # noqa: BLE001
        return None


def _generic(item: dict) -> dict:
    props = item.get("properties", {}) or {}
    source = props.get("source") or props.get("sources")
    return {
        "correlation_id": "",
        "title": (props.get("title") or item.get("id") or "")[:300],
        "description": props.get("description") or "",
        "roles": props.get("roles") or [],
        "country_codes": [],
        "source": source[:120] if isinstance(source, str) else "",
        "occurred_at": _dt(props.get("datetime") or props.get("start_datetime")),
        "ended_at": _dt(props.get("end_datetime")),
        "item_updated_at": _dt(props.get("updated")),
        "bbox": item.get("bbox"),
        "geom": _geom(item),
        "raw": item,
        "event_refs": refs.item_refs(item, monty=False),
    }


def _montandon(item: dict) -> dict:
    fields = _generic(item)
    props = item.get("properties", {}) or {}
    fields.update(
        {
            "correlation_id": props.get("monty:corr_id") or "",
            "country_codes": [c.upper() for c in (props.get("monty:country_codes") or [])],
            "event_refs": refs.item_refs(item, monty=True),
        }
    )
    return fields


_EXTRACTORS = {
    CatalogProfile.GENERIC: _generic,
    CatalogProfile.MONTANDON: _montandon,
}


def extract(profile: str, item: dict) -> dict:
    """StacItem field values (excluding catalog / collection / item_id) for a raw STAC item."""
    return _EXTRACTORS.get(profile, _generic)(item)
