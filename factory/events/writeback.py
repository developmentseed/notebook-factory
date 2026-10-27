"""
Use Case 3 write-back: publish a notebook-produced STAC collection to the
catalog the run's STAC item came from.

Contract with the notebook: write `stac/collection.json` and `stac/items/*.json`
into `output_dir`. Items should carry a `derived_from` link to the source item
(and `monty:corr_id` when writing to Montandon).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .stac_client import StacClient

log = logging.getLogger(__name__)


def collect_stac(outputs_dir: Path) -> tuple[dict | None, list[dict]]:
    stac_dir = outputs_dir / "stac"
    coll_path = stac_dir / "collection.json"
    if not coll_path.exists():
        return None, []
    collection = json.loads(coll_path.read_text())
    items = [json.loads(p.read_text()) for p in sorted((stac_dir / "items").glob("*.json"))]
    return collection, items


def write_back(run, outputs_dir: Path, client: StacClient | None = None) -> str | None:
    """Push the run's STAC output to its item's catalog. Returns the collection id written, or None if skipped."""
    collection, items = collect_stac(outputs_dir)
    if collection is None:
        return None
    catalog = run.stac_item.catalog if run.stac_item_id else None
    if catalog is None:
        run.log(f"write-back skipped: run has no STAC item, so no target catalog for {collection['id']}")
        return None
    if not catalog.writeback_enabled:
        run.log(
            f"write-back disabled for catalog {catalog.name}; would write {len(items)} items to {collection['id']}"
        )
        return None
    client = client or catalog.client()
    # Link every item back to this run's published page for provenance.
    for item in items:
        item.setdefault("links", []).append(
            {"rel": "about", "href": run.output_url, "type": "text/html", "title": "Analysis notebook"}
        )
        item.setdefault("collection", collection["id"])
    client.upsert_collection(collection)
    for item in items:
        client.upsert_item(collection["id"], item)
    return collection["id"]
