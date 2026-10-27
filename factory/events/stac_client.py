"""
A small client for STAC APIs (browse, search + transactions).

Only what the app needs: the run-form pickers, the poller and the write-back.
Notebooks talk to catalogs on their own (pystac-client, httpx) using
STAC_API_URL / STAC_API_KEY from their environment.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import datetime

import httpx
from django.conf import settings

log = logging.getLogger(__name__)


class StacClientError(RuntimeError):
    pass


class StacClient:
    def __init__(self, base_url: str, api_key: str = "", timeout: int | None = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or ""
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        self._client = httpx.Client(
            base_url=self.base_url,
            headers=headers,
            timeout=timeout or settings.STAC["TIMEOUT"],
            follow_redirects=True,
        )

    @classmethod
    def for_catalog(cls, catalog, timeout: int | None = None) -> StacClient:
        return cls(catalog.url, catalog.api_key, timeout=timeout)

    # -- reading --------------------------------------------------------------
    def _request(self, method: str, url: str, **kwargs) -> dict:
        try:
            r = self._client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise StacClientError(f"{method} {url}: {exc}") from exc
        if r.status_code >= 400:
            raise StacClientError(f"{method} {url} -> {r.status_code}: {r.text[:300]}")
        try:
            return r.json() if r.content else {}
        except ValueError as exc:
            raise StacClientError(f"{method} {url}: response is not JSON") from exc

    def root(self) -> dict:
        return self._request("GET", "/")

    def collections(self) -> list[dict]:
        """All collections (follows `next` links, which some APIs use for /collections)."""
        out: list[dict] = []
        page = self._request("GET", "/collections")
        while True:
            out.extend(page.get("collections", []))
            nxt = next((link for link in page.get("links", []) if link.get("rel") == "next"), None)
            if not nxt or len(out) > 5000:
                return out
            page = self._request("GET", nxt["href"])

    def get_item(self, collection: str, item_id: str) -> dict:
        return self._request("GET", f"/collections/{collection}/items/{item_id}")

    def search(
        self,
        collections: list[str],
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        bbox: list[float] | None = None,
        ids: list[str] | None = None,
        filter: dict | None = None,  # noqa: A002 - STAC API name
        limit: int = 100,
        max_items: int | None = None,
        sortby_field: str | None = "properties.datetime",
        sort_direction: str = "asc",
        extra: dict | None = None,
    ) -> Iterator[dict]:
        """Iterate over items (follows `next` links)."""
        body: dict = {"collections": collections, "limit": limit}
        if sortby_field:
            body["sortby"] = [{"field": sortby_field, "direction": sort_direction}]
        if since or until:
            start = since.isoformat().replace("+00:00", "Z") if since else ".."
            end = until.isoformat().replace("+00:00", "Z") if until else ".."
            body["datetime"] = f"{start}/{end}"
        if bbox:
            body["bbox"] = bbox
        if ids:
            body["ids"] = ids
        if filter:
            body["filter-lang"] = "cql2-json"
            body["filter"] = filter
        if extra:
            body.update(extra)
        seen = 0
        method, url = "POST", "/search"
        while True:
            if method == "POST":
                page = self._request("POST", url, json=body)
            else:
                page = self._request("GET", url)
            for feature in page.get("features", []):
                yield feature
                seen += 1
                if max_items and seen >= max_items:
                    return
            nxt = next((link for link in page.get("links", []) if link.get("rel") == "next"), None)
            if not nxt:
                return
            url = nxt["href"]
            method = nxt.get("method", "GET").upper()
            if method == "POST" and nxt.get("body"):
                body = {**body, **nxt["body"]} if nxt.get("merge") else nxt["body"]

    # -- transactions (write-back) --------------------------------------------
    def upsert_collection(self, collection: dict) -> dict:
        cid = collection["id"]
        try:
            self._request("GET", f"/collections/{cid}")
        except StacClientError:
            return self._request("POST", "/collections", json=collection)
        return self._request("PUT", f"/collections/{cid}", json=collection)

    def upsert_item(self, collection_id: str, item: dict) -> dict:
        iid = item["id"]
        try:
            self._request("GET", f"/collections/{collection_id}/items/{iid}")
        except StacClientError:
            return self._request("POST", f"/collections/{collection_id}/items", json=item)
        return self._request("PUT", f"/collections/{collection_id}/items/{iid}", json=item)

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
