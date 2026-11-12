"""Build `silver_disasters.yml`: event pairs labelled by the sources themselves, not by a reviewer.

GDACS publishes, for each of its events, the GLIDE number of the disaster (`glide`) and, for earthquakes, the
USGS event it was built from (`sourceid`). Montandon does not ingest these fields, so they make labels that
no scorer can see. This script lists GDACS Orange and Red alerts (earthquakes, tropical cyclones, floods),
reads those references from the GDACS API, finds the GDACS, USGS and GLIDE items in Montandon, and writes
them in the gold file's format: one disaster per GDACS event with its USGS event and every country record of
its GLIDE number. A GLIDE number cited by more than one GDACS event is left out (it happens for two
typhoons of one season), so no wrong negative is made from it.

Run it from this directory (takes about 15 minutes; it calls the public GDACS API politely):

    STAC_API_URL=https://montandon-eoapi.ifrc.org/stac STAC_API_KEY=... python build_silver.py
"""

from __future__ import annotations

import os
import sys
import time
from datetime import date
from pathlib import Path

import requests
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pairing as P  # noqa: E402

GDACS_API = "https://www.gdacs.org/gdacsapi/api/events"
HAZARDS = ("EQ", "TC", "FL")


def gdacs_alerts(start: date, end: date, timeout: int = 180) -> list[dict]:
    """GDACS Orange and Red alerts between two dates, as the properties of the API's features."""
    r = requests.get(
        f"{GDACS_API}/geteventlist/SEARCH",
        params={"eventlist": ";".join(HAZARDS), "alertlevel": "Orange;Red",
                "fromDate": start.isoformat(), "toDate": end.isoformat()},
        timeout=timeout,
    )  # fmt: skip
    r.raise_for_status()
    return [f["properties"] for f in r.json().get("features", [])]


def gdacs_refs(event_type: str, event_id: int | str, timeout: int = 60) -> dict:
    """{sourceid, glide, alertlevel, source} for one GDACS event, from its event data."""
    r = requests.get(
        f"{GDACS_API}/geteventdata", params={"eventtype": event_type, "eventid": event_id}, timeout=timeout
    )
    r.raise_for_status()
    p = r.json().get("properties") or {}
    return {k: p.get(k) or "" for k in ("sourceid", "glide", "alertlevel", "source")}


def glide_number(glide: str) -> tuple[str, str]:
    """("EQ", "2023-000166") from "EQ-2023-000166-MAR": the hazard and the number every country record shares."""
    parts = glide.split("-")
    return (parts[0], f"{parts[1]}-{parts[2]}") if len(parts) >= 3 else ("", "")


def silver_from_events(events: list[dict], found: dict[str, list[str]]) -> dict:
    """The gold-format dict from GDACS events and the Montandon items found for them.

    `events`: {eventid, eventtype, name, fromdate, countries, glide, sourceid}. `found`: {"gdacs:<eventid>":
    [item ids], "usgs:<sourceid>": [...], "glide:<hazard>-<number>": [...]}.
    """
    cited: dict[
        tuple[str, str], int
    ] = {}  # by number: two events may cite it with different country suffixes
    for e in events:
        if e["glide"]:
            cited[glide_number(e["glide"])] = cited.get(glide_number(e["glide"]), 0) + 1
    disasters = []
    for e in sorted(events, key=lambda e: (e["fromdate"], e["eventid"])):
        gdacs = found.get(f"gdacs:{e['eventid']}", [])[:1]
        usgs = found.get(f"usgs:{e['sourceid']}", []) if e["eventtype"] == "EQ" and e["sourceid"] else []
        glide = []
        if e["glide"] and cited[glide_number(e["glide"])] == 1:
            hazard, number = glide_number(e["glide"])
            glide = found.get(f"glide:{hazard}-{number}", [])
        members = gdacs + usgs + glide
        if gdacs and len(members) >= 2:
            disasters.append({
                "id": f"gdacs-{e['eventtype']}-{e['eventid']}",
                "name": e["name"],
                "start": e["fromdate"][:10],
                "countries": sorted(e["countries"]),
                "kind": "silver",
                "members": members,
            })  # fmt: skip
    _assign_windows(disasters)
    for d in disasters:
        d.pop("start")
        d.pop("countries")
    return {"version": 1, "disasters": disasters, "ambiguous": []}


def _assign_windows(disasters: list[dict], days: int = 30) -> None:
    """Disasters of one hazard sharing a country and starting within `days` share a window, so their records
    make negative pairs; any other disaster is a window of its own (positives only)."""
    parent = list(range(len(disasters)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, a in enumerate(disasters):
        for j in range(i + 1, len(disasters)):
            b = disasters[j]
            gap = (date.fromisoformat(b["start"]) - date.fromisoformat(a["start"])).days
            if gap > days:
                break
            if a["id"].split("-")[1] == b["id"].split("-")[1] and set(a["countries"]) & set(b["countries"]):
                parent[root(j)] = root(i)
    for i, d in enumerate(disasters):
        first = disasters[root(i)]
        d["window"] = f"{first['id'].split('-')[1]}-{first['start']}-{(first['countries'] or ['UNK'])[0]}"


def _montandon(api_url: str, token: str, collection: str, src_ids: list[str]) -> list[dict]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    items = []
    for i in range(0, len(src_ids), 20):
        body = {
            "collections": [collection], "limit": 1000, "filter-lang": "cql2-json",
            "filter": {"op": "in", "args": [{"property": "monty:src_event_id"}, src_ids[i : i + 20]]},
        }  # fmt: skip
        url, payload = f"{api_url.rstrip('/')}/search", body
        while url:  # a cyclone has an item per GDACS episode, so a page can fill up
            r = requests.post(url, json=payload, headers=headers, timeout=120)
            r.raise_for_status()
            page = r.json()
            items += page.get("features", [])
            nxt = next((link for link in page.get("links", []) if link.get("rel") == "next"), None)
            url, payload = (nxt["href"], nxt.get("body") or body) if nxt else (None, None)
    return items


def main(first_year: int = 2015, last: date | None = None) -> None:
    api_url, token = os.environ["STAC_API_URL"], os.environ.get("STAC_API_KEY", "")
    last = last or date.today()
    events: dict[int, dict] = {}
    for year in range(first_year, last.year + 1):
        for q in range(4):
            start, end = date(year, 3 * q + 1, 1), date(year + (q == 3), (3 * q + 3) % 12 + 1, 1)
            if start > last:
                break
            for p in gdacs_alerts(start, min(end, last)):
                events.setdefault(int(p["eventid"]), {
                    "eventid": int(p["eventid"]), "eventtype": p["eventtype"], "name": p.get("name") or "",
                    "fromdate": p.get("fromdate") or "", "glide": p.get("glide") or "", "sourceid": "",
                    "countries": [c["iso3"] for c in p.get("affectedcountries") or [] if c.get("iso3")],
                })  # fmt: skip
            time.sleep(1)
        print(year, len(events), "events", flush=True)
    for e in events.values():
        if e["eventtype"] == "EQ":
            e["sourceid"] = gdacs_refs("EQ", e["eventid"])["sourceid"]
            time.sleep(0.5)

    found: dict[str, list[str]] = {}
    for item in _montandon(
        api_url, token, "gdacs-events", sorted({str(e["eventid"]) for e in events.values()})
    ):
        found.setdefault(f"gdacs:{item['properties']['monty:src_event_id']}", []).append(item["id"])
    for key in found:  # a stable choice among the episodes of one GDACS event: the first episode
        found[key].sort(key=lambda i: int(i.rsplit("-", 1)[1]))
    usgs_ids = sorted({f"usgs-event-{e['sourceid']}" for e in events.values() if e["sourceid"]})
    for item_id in P.fetch_items(api_url, token, usgs_ids):
        found.setdefault(f"usgs:{item_id.removeprefix('usgs-event-')}", []).append(item_id)
    numbers = sorted({glide_number(e["glide"])[1] for e in events.values() if e["glide"]} - {""})
    for item in _montandon(api_url, token, "glide-events", numbers):
        hazard = item["id"].removeprefix("glide-event-").split("-")[0]
        found.setdefault(f"glide:{hazard}-{item['properties']['monty:src_event_id']}", []).append(item["id"])

    silver = silver_from_events(list(events.values()), found)
    header = (
        "# Silver set for the event-pairing benchmark, written by build_silver.py: do not edit by hand.\n"
        "# Labels come from GDACS's own references (`glide`, and `sourceid` = the USGS event for earthquakes),\n"
        f"# for GDACS Orange and Red alerts {first_year} to {last.isoformat()}. Same format as gold_disasters.yml.\n"
        "# Where GDACS gives an aftershock its own GLIDE number, it is its own disaster here, as the registers say.\n"
    )
    out = Path(__file__).resolve().parent / "silver_disasters.yml"
    out.write_text(header + yaml.safe_dump(silver, sort_keys=False, allow_unicode=True, width=120))
    n = sum(len(d["members"]) for d in silver["disasters"])
    print(f"wrote {out.name}: {len(silver['disasters'])} disasters, {n} records")


if __name__ == "__main__":
    main()
