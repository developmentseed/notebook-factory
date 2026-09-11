"""
Use Case 3 write-back: publish a notebook-produced STAC collection to Montandon.

Contract with the notebook: write `stac/collection.json` and `stac/items/*.json`
into `output_dir`. Items should carry `monty:corr_id` and a `derived_from` link.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from django.conf import settings

from .montandon import MontandonClient

log = logging.getLogger(__name__)


def collect_stac(outputs_dir: Path) -> tuple[dict | None, list[dict]]:
    stac_dir = outputs_dir / "stac"
    coll_path = stac_dir / "collection.json"
    if not coll_path.exists():
        return None, []
    collection = json.loads(coll_path.read_text())
    items = [json.loads(p.read_text()) for p in sorted((stac_dir / "items").glob("*.json"))]
    return collection, items


def write_back(run, outputs_dir: Path, client: MontandonClient | None = None) -> str | None:
    """Push the run's STAC output to Montandon. Returns the collection id written, or None if skipped."""
    collection, items = collect_stac(outputs_dir)
    if collection is None:
        return None
    if not settings.MONTANDON["WRITEBACK_ENABLED"]:
        run.log(
            f"write-back disabled (MONTANDON_WRITEBACK_ENABLED=false); would write {len(items)} items to {collection['id']}"
        )
        return None
    client = client or MontandonClient()
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
