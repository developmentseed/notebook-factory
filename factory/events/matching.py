"""Turn STAC items into StacItem rows and decide which rules they fire."""

from __future__ import annotations

from jinja2 import Environment, StrictUndefined

from .models import StacCatalog, StacItem, TriggerRule
from .profiles import extract

_jinja = Environment(undefined=StrictUndefined, autoescape=False)


def upsert_item(
    catalog: StacCatalog, item: dict, collection: str | None = None
) -> tuple[StacItem, bool, bool]:
    """Create or update a StacItem from a raw STAC item. Returns (stac_item, created, changed)."""
    collection = collection or item.get("collection") or ""
    fields = extract(catalog.profile, item)
    existing = StacItem.objects.filter(catalog=catalog, collection=collection, item_id=item["id"]).first()
    if existing is None:
        obj = StacItem.objects.create(catalog=catalog, collection=collection, item_id=item["id"], **fields)
        return obj, True, True
    changed = (existing.item_updated_at != fields["item_updated_at"]) or (existing.raw != item)
    for k, v in fields.items():
        setattr(existing, k, v)
    existing.save()
    return existing, False, changed


def fetch_item(catalog: StacCatalog, collection: str, item_id: str) -> StacItem:
    """Fetch one item from the catalog and store it. Raises StacClientError."""
    with catalog.client() as client:
        raw = client.get_item(collection, item_id)
    obj, _, _ = upsert_item(catalog, raw, collection=collection)
    return obj


def rule_matches(rule: TriggerRule, item: StacItem) -> bool:
    if not rule.enabled:
        return False
    if rule.catalog_id != item.catalog_id:
        return False
    if rule.collections and item.collection not in rule.collections:
        return False
    if rule.roles and not set(rule.roles) & set(item.roles or []):
        return False
    if rule.country_codes and not set(c.upper() for c in rule.country_codes) & set(item.country_codes or []):
        return False
    hazards = list(rule.hazards.all())
    if hazards:
        if not any(h.matches_code(code) for h in hazards for code in (item.hazard_codes or [])):
            return False
    return True


def render_parameters(template: dict, item: StacItem, area=None) -> dict:
    """Render Jinja placeholders in string values of the parameter template.

    `item` is the StacItem; `event` is kept as an alias so older rule templates still render.
    """
    context = {
        "item": item,
        "event": item,
        "raw": item.raw or {},
        "props": (item.raw or {}).get("properties", {}),
        "hazard": item.hazard.key if item.hazard_id else "",
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
