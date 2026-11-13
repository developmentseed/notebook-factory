"""Build `hazard_links.csv`: which hazards trigger which, from the UNDRR-ISC 2025 Hazard Information Profiles.

The HIPs API (https://data.undrr.org/api/json/hips/hazards/2.0.0/API) returns every profile with two lists,
taken from its "multi-hazard context" figure: `triggering_hips`, the hazards it triggers, and `triggered_hips`,
the hazards that trigger it. Each link is written once as `source triggers target`, with the profile(s) that state
it. The pairing benchmark uses the links in both directions to decide which hazards are related.

Run it from this directory (one request of about 4 MB):

    python build_hazard_links.py
"""

from __future__ import annotations

import csv
from pathlib import Path

import requests

API = "https://data.undrr.org/api/json/hips/hazards/2.0.0/API"


def fetch_profiles(timeout: int = 120) -> list[dict]:
    profiles, page = [], 1
    while True:
        r = requests.get(API, params={"page": page, "limit": 500}, timeout=timeout)
        r.raise_for_status()
        body = r.json()
        batch = body if isinstance(body, list) else body.get("data") or body.get("items") or []
        profiles += batch
        if isinstance(body, list) or len(batch) < 500:
            return profiles
        page += 1


def links_from_profiles(profiles: list[dict]) -> list[dict]:
    """[{source, target, stated_by}] sorted: source triggers target, stated by one or both profiles."""
    stated: dict[tuple[str, str], set[str]] = {}
    for p in profiles:
        code = p.get("notation")
        if not code:
            continue
        for target in p.get("triggering_hips") or []:
            stated.setdefault((code, target), set()).add(code)
        for source in p.get("triggered_hips") or []:
            stated.setdefault((source, code), set()).add(code)
    return [
        {"source": s, "target": t, "stated_by": " ".join(sorted(by))}
        for (s, t), by in sorted(stated.items())
        if s != t
    ]


def main() -> None:
    profiles = fetch_profiles()
    links = links_from_profiles(profiles)
    out = Path(__file__).resolve().parent / "hazard_links.csv"
    with out.open("w", newline="") as f:
        f.write(
            f"# Built by build_hazard_links.py from {API} ({len(profiles)} profiles). Do not edit by hand.\n"
        )
        w = csv.DictWriter(f, fieldnames=["source", "target", "stated_by"])
        w.writeheader()
        w.writerows(links)
    print(f"wrote {out.name}: {len(links)} links from {len(profiles)} profiles")


if __name__ == "__main__":
    main()
