"""Turn STAC items into Event rows and decide which rules they fire."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django.contrib.gis.geos import GEOSGeometry
from django.utils.dateparse import parse_datetime
from jinja2 import Environment, StrictUndefined

from factory.catalog.models import Hazard

from .models import Event, EventTriggerRule

_jinja = Environment(undefined=StrictUndefined, autoescape=False)


def _dt(value: Any) -> datetime | None:
    if not value:
        return None
    dt = parse_datetime(str(value))
    if dt and dt.tzinfo is None:
        from django.utils import timezone

        dt = timezone.make_aware(dt)
    return dt


def upsert_event(item: dict) -> tuple[Event, bool, bool]:
    """Create or update an Event from a STAC item. Returns (event, created, changed)."""
    props = item.get("properties", {}) or {}
    hazard_codes = props.get("monty:hazard_codes") or []
    geom = None
    if item.get("geometry"):
        try:
            geom = GEOSGeometry(__import__("json").dumps(item["geometry"]), srid=4326)
        except Exception:  # noqa: BLE001
            geom = None
    fields = {
        "monty_corr_id": props.get("monty:corr_id") or "",
        "title": (props.get("title") or item.get("id") or "")[:300],
        "description": props.get("description") or "",
        "roles": props.get("roles") or [],
        "hazard": Hazard.for_codes(hazard_codes),
        "hazard_codes": hazard_codes,
        "country_codes": [c.upper() for c in (props.get("monty:country_codes") or [])],
        "source": (props.get("source") or props.get("sources") or "")[:120]
        if isinstance(props.get("source") or props.get("sources"), str)
        else "",
        "occurred_at": _dt(props.get("datetime") or props.get("start_datetime")),
        "ended_at": _dt(props.get("end_datetime")),
        "item_updated_at": _dt(props.get("updated")),
        "bbox": item.get("bbox"),
        "geom": geom,
        "raw": item,
    }
    existing = Event.objects.filter(collection=item.get("collection", ""), stac_id=item["id"]).first()
    if existing is None:
        event = Event.objects.create(collection=item.get("collection", ""), stac_id=item["id"], **fields)
        return event, True, True
    changed = (existing.item_updated_at != fields["item_updated_at"]) or (existing.raw != item)
    for k, v in fields.items():
        setattr(existing, k, v)
    existing.save()
    return existing, False, changed


def rule_matches(rule: EventTriggerRule, event: Event) -> bool:
    if not rule.enabled:
        return False
    if rule.collections and event.collection not in rule.collections:
        return False
    if rule.roles and not set(rule.roles) & set(event.roles or []):
        return False
    if rule.country_codes and not set(c.upper() for c in rule.country_codes) & set(event.country_codes or []):
        return False
    hazards = list(rule.hazards.all())
    if hazards:
        if not any(h.matches_code(code) for h in hazards for code in (event.hazard_codes or [])):
            return False
    return True


def render_parameters(template: dict, event: Event, area=None) -> dict:
    """Render Jinja placeholders in string values of the parameter template."""
    context = {
        "event": event,
        "item": event.raw or {},
        "props": (event.raw or {}).get("properties", {}),
        "hazard": event.hazard.key if event.hazard_id else "",
        "area": area,
    }

    def render_value(value):
        if isinstance(value, str) and "{{" in value:
            return _jinja.from_string(value).render(**context)
        if isinstance(value, dict):
            return {k: render_value(v) for k, v in value.items()}
        if isinstance(value, list):
            return [render_value(v) for v in value]
        return value

    return {k: render_value(v) for k, v in (template or {}).items()}
