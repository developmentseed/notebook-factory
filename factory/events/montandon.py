"""
A small client for the Montandon STAC API (search + transactions).

Only what the poller and the write-back need; notebooks talk to Montandon on
their own with pystac-client.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import datetime

import httpx
from django.conf import settings

log = logging.getLogger(__name__)


class MontandonError(RuntimeError):
    pass


class MontandonClient:
    def __init__(self, base_url: str | None = None, token: str | None = None, timeout: int | None = None):
        cfg = settings.MONTANDON
        self.base_url = (base_url or cfg["STAC_URL"]).rstrip("/")
        self.token = token if token is not None else cfg["API_TOKEN"]
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        self._client = httpx.Client(
            base_url=self.base_url, headers=headers, timeout=timeout or cfg["TIMEOUT"], follow_redirects=True
        )

    # -- reading --------------------------------------------------------------
    def _request(self, method: str, url: str, **kwargs) -> dict:
        try:
            r = self._client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise MontandonError(f"{method} {url}: {exc}") from exc
        if r.status_code >= 400:
            raise MontandonError(f"{method} {url} -> {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else {}

    def collections(self) -> list[dict]:
        return self._request("GET", "/collections").get("collections", [])

    def get_item(self, collection: str, item_id: str) -> dict:
        return self._request("GET", f"/collections/{collection}/items/{item_id}")

    def search(
        self,
        collections: list[str],
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        bbox: list[float] | None = None,
        limit: int = 100,
        max_items: int | None = None,
        sortby_field: str = "properties.datetime",
        extra: dict | None = None,
    ) -> Iterator[dict]:
        """Iterate over items (follows `next` links)."""
        body: dict = {
            "collections": collections,
            "limit": limit,
            "sortby": [{"field": sortby_field, "direction": "asc"}],
        }
        if since or until:
            start = since.isoformat().replace("+00:00", "Z") if since else ".."
            end = until.isoformat().replace("+00:00", "Z") if until else ".."
            body["datetime"] = f"{start}/{end}"
        if bbox:
            body["bbox"] = bbox
        if extra:
            body.update(extra)
        seen = 0
        url = "/search"
        while True:
            page = self._request("POST", url, json=body)
            for feature in page.get("features", []):
                yield feature
                seen += 1
                if max_items and seen >= max_items:
                    return
            nxt = next((link for link in page.get("links", []) if link.get("rel") == "next"), None)
            if not nxt:
                return
            if nxt.get("method", "GET").upper() == "POST" and nxt.get("body"):
                body = {**body, **nxt["body"]} if nxt.get("merge") else nxt["body"]
                url = nxt["href"]
            else:
                page = self._request("GET", nxt["href"])
                # Simplest handling for GET-style pagination: process this page and continue.
                for feature in page.get("features", []):
                    yield feature
                    seen += 1
                    if max_items and seen >= max_items:
                        return
                nxt2 = next((link for link in page.get("links", []) if link.get("rel") == "next"), None)
                if not nxt2:
                    return
                url = nxt2["href"]
                body = nxt2.get("body", body)

    # -- transactions (write-back) --------------------------------------------
    def upsert_collection(self, collection: dict) -> dict:
        cid = collection["id"]
        try:
            self._request("GET", f"/collections/{cid}")
        except MontandonError:
            return self._request("POST", "/collections", json=collection)
        return self._request("PUT", f"/collections/{cid}", json=collection)

    def upsert_item(self, collection_id: str, item: dict) -> dict:
        iid = item["id"]
        try:
            self._request("GET", f"/collections/{collection_id}/items/{iid}")
        except MontandonError:
            return self._request("POST", f"/collections/{collection_id}/items", json=item)
        return self._request("PUT", f"/collections/{collection_id}/items/{iid}", json=item)

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
