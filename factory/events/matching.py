"""Turn STAC items into StacItem rows and decide which rules they fire."""

from __future__ import annotations

from jinja2 import Environment, StrictUndefined

from factory.catalog import facets

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
        obj.facet_options.set(facets.for_stac_item(item))
        return obj, True, True
    changed = (existing.item_updated_at != fields["item_updated_at"]) or (existing.raw != item)
    for k, v in fields.items():
        setattr(existing, k, v)
    existing.save()
    existing.facet_options.set(facets.for_stac_item(item))
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
    wanted = facets.group_options(rule.facet_options.all())
    if wanted:
        have = {o.pk for o in item.facet_options.all()}
        if not all(have & {o.pk for o in options} for _, options in wanted):
            return False
    return True


def render_parameters(template: dict, item: StacItem, area=None) -> dict:
    """Render Jinja placeholders in string values of the parameter template.

    `item` is the StacItem; `event` is kept as an alias so older rule templates still render.
    `filters` is {facet_key: [option keys]} of the item; `hazard` is its first hazard key.
    """
    item_filters = facets.option_keys(item.facet_options.select_related("facet"))
    context = {
        "item": item,
        "event": item,
        "raw": item.raw or {},
        "props": (item.raw or {}).get("properties", {}),
        "filters": item_filters,
        "hazard": (item_filters.get("hazard") or [""])[0],
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
