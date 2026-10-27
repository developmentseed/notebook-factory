"""
View-model for the run form's STAC picker: catalog → collection → item.

The whole picker is one HTMX partial that re-renders from its own inputs, so
there is a single place deciding what to list. Template `stac_hints`
({profile, collections, roles}) narrow and preselect the lists unless the user
ticks "show all"; they are never enforced on submit.
"""

from __future__ import annotations

from django.conf import settings
from django.core.cache import cache
from django.urls import reverse

from factory.events.models import StacCatalog
from factory.events.stac_client import StacClientError

ITEM_LIMIT = 25


def catalog_collections(catalog: StacCatalog) -> list[dict]:
    """[{id, title}] for a catalog, cached; editing the catalog invalidates the cache."""
    key = f"stac:collections:{catalog.pk}:{catalog.updated_at.timestamp()}"
    cached = cache.get(key)
    if cached is not None:
        return cached
    with catalog.client() as client:
        raw = client.collections()
    out = sorted(
        ({"id": c["id"], "title": c.get("title") or c["id"]} for c in raw if c.get("id")),
        key=lambda c: c["id"],
    )
    cache.set(key, out, settings.STAC["COLLECTIONS_CACHE_SECONDS"])
    return out


def _search_items(catalog: StacCatalog, collection: str, query: str) -> list[dict]:
    with catalog.client() as client:
        if query:
            return list(client.search([collection], ids=[query], limit=ITEM_LIMIT, max_items=ITEM_LIMIT))
        try:
            return list(
                client.search([collection], sort_direction="desc", limit=ITEM_LIMIT, max_items=ITEM_LIMIT)
            )
        except StacClientError:
            # Not every API supports sortby; fall back to the catalog's default order.
            return list(
                client.search([collection], sortby_field=None, limit=ITEM_LIMIT, max_items=ITEM_LIMIT)
            )


def _item_row(raw: dict) -> dict:
    props = raw.get("properties", {}) or {}
    return {
        "id": raw.get("id", ""),
        "title": props.get("title") or "",
        "datetime": props.get("datetime") or props.get("start_datetime") or "",
        "roles": props.get("roles") or [],
    }


def build(notebook, params) -> dict:
    """Template context for `web/partials/stac_picker.html` from GET/POST-like `params`.

    `notebook` is None for uploads: no hints, and the picker re-renders from the upload URL.
    """
    hints = (notebook.stac_hints if notebook else None) or {}
    url = reverse("web:stac-picker", args=[notebook.slug]) if notebook else reverse("web:upload-stac-picker")
    show_all = params.get("stac_show_all") in {"1", "on", "true"}
    ctx: dict = {
        "nb": notebook,
        "url": url,
        "hints": hints,
        "show_all": show_all,
        "catalogs": [],
        "catalog": None,
        "collections": [],
        "collection": (params.get("stac_collection") or "").strip(),
        "items": [],
        "hidden_by_roles": 0,
        "item_id": (params.get("stac_item_id") or "").strip(),
        "query": (params.get("stac_q") or "").strip(),
        "notes": [],
        "error": "",
    }

    catalogs = list(StacCatalog.objects.filter(is_active=True))
    if hints.get("profile") and not show_all:
        matching = [c for c in catalogs if c.profile == hints["profile"]]
        if matching:
            catalogs = matching
        elif catalogs:
            ctx["notes"].append(f"No {hints['profile']} catalog is configured; showing all catalogs.")
    ctx["catalogs"] = catalogs
    if not catalogs:
        return ctx
    selected = str(params.get("stac_catalog") or "")
    catalog = next((c for c in catalogs if str(c.pk) == selected), None) or catalogs[0]
    ctx["catalog"] = catalog

    try:
        collections = catalog_collections(catalog)
    except StacClientError as exc:
        ctx["error"] = f"Could not list collections: {exc}"
        return ctx
    hinted = set(hints.get("collections") or [])
    if hinted and not show_all:
        narrowed = [c for c in collections if c["id"] in hinted]
        if narrowed:
            collections = narrowed
    ctx["collections"] = collections
    ids = {c["id"] for c in collections}
    if ctx["collection"] not in ids:
        ctx["collection"] = next((c["id"] for c in collections if c["id"] in hinted), "")
    if not ctx["collection"]:
        return ctx

    try:
        rows = [_item_row(raw) for raw in _search_items(catalog, ctx["collection"], ctx["query"])]
    except StacClientError as exc:
        ctx["error"] = f"Could not search items: {exc}"
        return ctx
    roles = set(hints.get("roles") or [])
    if roles and not show_all:
        kept = [r for r in rows if roles & set(r["roles"])]
        ctx["hidden_by_roles"] = len(rows) - len(kept)
        rows = kept
    ctx["items"] = rows
    return ctx
