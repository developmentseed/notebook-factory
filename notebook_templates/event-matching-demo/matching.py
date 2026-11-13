"""Helpers for the event-matching demo: find the same disaster in other Montandon sources, join what they say.

Pure Python + pandas (no Django). The only network calls are the Montandon STAC API (`_search`) and OpenRouter's
Decisions API (`call_decisions`), so tests can mock both. The rules are adapted from the event-pairing benchmark.
"""

from __future__ import annotations

import html
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pandas as pd
import requests

# -- the rules a user can change (template parameters) ---------------------------------------------------------
# GLIDE hazard code -> (days either side of the event, radius in km) in which another source's record is a candidate.
HAZARD_WINDOWS = {
    "EQ": (7, 300), "TS": (7, 1500), "VO": (30, 200), "LS": (7, 100), "AV": (7, 100), "FL": (30, 500),
    "FF": (14, 300), "TC": (21, 2500), "ST": (14, 800), "SS": (14, 800), "DR": (180, 2000), "WF": (30, 300),
    "HT": (30, 1500), "CW": (30, 1500), "EP": (90, 3000),
}  # fmt: skip
DEFAULT_WINDOWS_TEXT = "\n".join(f"{code} {days} {km}" for code, (days, km) in HAZARD_WINDOWS.items())
RULE_PARAMETERS = (
    "hazard_windows", "default_window_days", "default_radius_km", "coarse_radius_km", "min_cross_hazard_magnitude",
)  # fmt: skip


def parse_windows(text: str) -> dict[str, tuple[int, int]]:
    """{"EQ": (7, 300), ...} from lines of `CODE days km`. Blank lines and `#` comments are ignored."""
    out = {}
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#")[0].strip()
        if not line:
            continue
        code, *rest = line.split()
        if (
            len(rest) != 2
            or not re.fullmatch(r"[A-Za-z]{2}", code)
            or not all(x.isdigit() and int(x) > 0 for x in rest)
        ):
            raise ValueError(
                f"hazard_windows line {n}: expected 'CODE days km' (for example 'EQ 7 300'), got {line!r}"
            )
        out[code.upper()] = (int(rest[0]), int(rest[1]))
    return out


@dataclass(frozen=True)
class Rules:
    """When is another source's record a candidate for the selected event? All of it comes from the run form."""

    windows: dict = field(default_factory=lambda: dict(HAZARD_WINDOWS))
    default_days: int = 14  # for a hazard that is not in `windows`
    default_radius_km: int = 500
    coarse_radius_km: int = 1500  # at least this radius when a record is only located at country level
    min_cross_hazard_magnitude: float = 5.0  # a smaller quake (no tsunami flag) is only compared with quakes

    @classmethod
    def from_params(
        cls,
        hazard_windows,
        default_window_days,
        default_radius_km,
        coarse_radius_km,
        min_cross_hazard_magnitude,
    ):
        for name, value in (("default_window_days", default_window_days), ("default_radius_km", default_radius_km),
                            ("coarse_radius_km", coarse_radius_km)):  # fmt: skip
            if value < 1:
                raise ValueError(f"{name} must be at least 1")
        return cls(parse_windows(hazard_windows), int(default_window_days), int(default_radius_km),
                   int(coarse_radius_km), float(min_cross_hazard_magnitude))  # fmt: skip

    def window(self, rec: Record) -> tuple[int, int]:
        return self.windows.get(rec.glide, (self.default_days, self.default_radius_km))


# -- constants -----------------------------------------------------------------------------------------------------
STRONG, POSSIBLE = (
    0.7,
    0.4,
)  # the app's "joins the disaster" and "possibly" thresholds, used to rank candidates
# Sources that only locate an event at country level and date it to the day.
COARSE_SOURCES = {"glide", "emdat", "ifrcevent", "idmc-idu", "idmc-gidd", "desinventar"}
FAMILIES = {"idmc-idu": "idmc", "idmc-gidd": "idmc"}  # IDMC publishes one event under both collections
UNDRR_RE = re.compile(r"^[A-Z]{2}\d{4}$")
PDC_MAGNITUDE_RE = re.compile(r"Earthquake.*? - (\d+(?:\.\d+)?) - ")
UNKNOWN_COUNTRY = "UNK"
_PREFIXES = (
    "emdat", "glide", "ifrcevent", "idmc-idu", "idmc-gidd", "cems", "charter", "usgs", "gdacs", "pdc",
    "desinventar", "gfd",
)  # fmt: skip
EVENT_COLLECTIONS = [f"{p}-events" for p in _PREFIXES] + ["ibtracs-events"]
DEFAULT_MODEL = "typesafe/jev-1.14-20261014"
DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"

# Column order of the fact sheet; the labels are also what the chart and the map legend show.
SOURCE_LABELS = {
    "usgs": "USGS", "emdat": "EM-DAT", "ifrcevent": "IFRC", "idmc-idu": "IDMC (IDU)", "idmc-gidd": "IDMC (GIDD)",
    "pdc": "PDC", "gdacs": "GDACS", "cems": "CEMS", "charter": "Charter", "glide": "GLIDE", "ibtracs": "IBTrACS",
    "desinventar": "DesInventar", "gfd": "GFD",
}  # fmt: skip
SOURCE_COLORS = {
    "usgs": "#0072B2", "emdat": "#D55E00", "ifrcevent": "#CC3311", "idmc-idu": "#009E73", "idmc-gidd": "#66C2A5",
    "pdc": "#E69F00", "gdacs": "#8E6BBF", "cems": "#332288", "charter": "#6F4E37", "glide": "#7A7A7A",
    "ibtracs": "#56B4E9", "desinventar": "#B5651D", "gfd": "#44AA99",
}  # fmt: skip


# -- records -------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Record:
    """One Montandon event item, reduced to what matching looks at."""

    id: str
    source: str
    title: str
    text: str
    start: datetime | None
    end: datetime | None
    countries: tuple[str, ...]
    hazard_codes: tuple[str, ...]
    point: tuple[float, float] | None  # (lon, lat)
    coarse: bool  # the geometry is a country outline or point, the date a day
    magnitude: float | None
    tsunami: bool = False
    links: tuple[tuple[str, str, str], ...] = ()  # (role, collection, item id) of the item's related links
    src_event_id: str = ""  # monty:src_event_id: the source's own id, shared by the episodes of one event
    corr_id: str = ""  # monty:corr_id: Montandon's correlation id (date, country, number, hazard, episode)
    episode: object = None  # monty:episode_number
    geometry: dict | None = field(default=None, compare=False)

    @property
    def family(self) -> str:
        return FAMILIES.get(self.source, self.source)

    @property
    def glide(self) -> str:
        return next((c for c in self.hazard_codes if len(c) == 2 and c.isalpha()), "")

    @property
    def undrr(self) -> str:
        return next((c for c in self.hazard_codes if UNDRR_RE.match(c)), "")


def _dt(value) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)


def _centroid(geom: dict | None) -> tuple[float, float] | None:
    if not geom:
        return None
    coords = geom.get("coordinates")
    if geom.get("type") == "Point" and coords:
        return float(coords[0]), float(coords[1])
    points: list = []

    def walk(c):
        if c and isinstance(c[0], int | float):
            points.append(c)
        else:
            for x in c or []:
                walk(x)

    walk(coords)
    return (
        (sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points)) if points else None
    )


def _magnitude(source: str, props: dict) -> float | None:
    try:
        if source == "usgs" and props.get("eq:magnitude") is not None:
            return float(props["eq:magnitude"])
        severity = props.get("severitydata") or {}
        if source == "gdacs" and severity.get("severityunit") == "M":
            return float(severity.get("severity"))
        if source == "pdc" and (m := PDC_MAGNITUDE_RE.search(props.get("title") or "")):
            return float(m.group(1))
    except (TypeError, ValueError):
        return None
    return None


def clean_text(value: str | None, limit: int = 600) -> str:
    """Plain text from a description: no HTML, markdown images or URLs; whitespace collapsed."""
    s = re.sub(r"!\[[^\]]*\]\([^)]*\)|https?://\S+|<[^>]+>", " ", html.unescape(value or ""))
    return re.sub(r"\s+", " ", s).strip()[:limit]


def _related_links(item: dict) -> tuple[tuple[str, str, str], ...]:
    out = []
    for link in item.get("links") or []:
        target = re.search(r"/collections/([^/]+)/items/([^/?#]+)", link.get("href") or "")
        if link.get("rel") == "related" and target:
            role = next((r for r in link.get("roles") or [] if r in ("event", "hazard", "impact")), "")
            out.append((role, target.group(1), target.group(2)))
    return tuple(out)


def record_from_item(item: dict) -> Record:
    props = item.get("properties") or {}
    if not item.get("collection"):
        raise ValueError(f"item {item.get('id')!r} has no collection")
    source = item["collection"].removesuffix("-events")
    start = _dt(props.get("start_datetime") or props.get("datetime"))
    point = _centroid(item.get("geometry"))
    return Record(
        id=item["id"],
        source=source,
        title=clean_text(props.get("title") or item["id"], 200),
        text=clean_text(props.get("description")),
        start=start,
        end=_dt(props.get("end_datetime")) or start,
        countries=tuple(c.upper() for c in props.get("monty:country_codes") or []),
        hazard_codes=tuple(props.get("monty:hazard_codes") or []),
        point=point,
        coarse=source in COARSE_SOURCES or point is None,
        magnitude=_magnitude(source, props),
        tsunami=str(props.get("eq:tsunami", "")).lower() == "true",
        links=_related_links(item),
        src_event_id=str(props.get("monty:src_event_id") or ""),
        corr_id=str(props.get("monty:corr_id") or ""),
        episode=props.get("monty:episode_number"),
        geometry=item.get("geometry"),
    )


# -- the STAC API ----------------------------------------------------------------------------------------------------
def _search(api: str, token: str, body: dict, max_items: int = 600, timeout: int = 90) -> list[dict]:
    """The features of POST {api}/search, following `next` links until `max_items`."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    features: list[dict] = []
    while len(features) < max_items:
        r = requests.post(f"{api.rstrip('/')}/search", json=body, headers=headers, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        features += data.get("features") or []
        nxt = next((x for x in data.get("links") or [] if x.get("rel") == "next" and x.get("body")), None)
        if not nxt or not data.get("features"):
            break
        body = {**body, **nxt["body"]} if nxt.get("merge") else nxt["body"]
    return features[:max_items]


# -- candidates --------------------------------------------------------------------------------------------------------
def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lon1, lat1, lon2, lat2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def gap_days(a: Record, b: Record, by_day: bool) -> float:
    """Days between the two records' date ranges (0 when they overlap); by calendar day for coarse records."""
    if not (a.start and b.start):
        return 0.0
    a0, a1, b0, b1 = a.start, a.end or a.start, b.start, b.end or b.start
    if by_day:
        a0, a1, b0, b1 = (d.date() for d in (a0, a1, b0, b1))
        return max(b0 - a1, a0 - b1, timedelta(0)).days
    return max(b0 - a1, a0 - b1, timedelta(0)).total_seconds() / 86400


def pair_anchor(a: Record, b: Record) -> tuple[Record, Record]:
    """(anchor, candidate): the precise record sets the window and radius, else the earlier one."""
    if a.coarse != b.coarse:
        return (b, a) if a.coarse else (a, b)
    first = datetime.max.replace(tzinfo=UTC)
    return (a, b) if (a.start or first, a.id) <= (b.start or first, b.id) else (b, a)


def _radius(anchor: Record, cand: Record, rules: Rules) -> int:
    radius = rules.window(anchor)[1]
    return max(radius, rules.coarse_radius_km) if anchor.coarse or cand.coarse else radius


def proximity(
    anchor: Record, cand: Record, rules: Rules = Rules()
) -> tuple[float, float, float, float | None]:
    """(time score, distance score, gap in days, km): the two terms the app's heuristic averages."""
    days = rules.window(anchor)[0]
    gap = gap_days(anchor, cand, by_day=anchor.coarse or cand.coarse)
    t = max(0.0, 1 - gap / days)
    if anchor.point and cand.point:
        km = haversine_km(anchor.point, cand.point)
        return t, max(0.0, 1 - km / _radius(anchor, cand, rules)), gap, km
    return t, 0.5, gap, None


def heuristic_score(anchor: Record, cand: Record, rules: Rules = Rules()) -> float:
    """Time and distance, as in the app: used here to rank candidates and to tell Jev how far apart they are."""
    t, d, _, _ = proximity(anchor, cand, rules)
    s = (t + d) / 2
    if not (t and d):
        s = min(s, POSSIBLE - 0.1)  # right place at the wrong time (or the reverse) is not a match
    if anchor.undrr and cand.undrr and anchor.undrr != cand.undrr:
        s *= 0.9
    return round(s, 2)


def within_radius(a: Record, b: Record, rules: Rules = Rules()) -> bool:
    anchor, cand = pair_anchor(a, b)
    km = proximity(anchor, cand, rules)[3]
    return km is not None and km <= _radius(anchor, cand, rules)


def is_quake(rec: Record) -> bool:
    return rec.glide == "EQ" or rec.undrr.startswith("GH01")


def quake_can_trigger(a: Record, b: Record, rules: Rules = Rules()) -> bool:
    """False for a pair of an earthquake and another hazard when the quake is too small to have caused it:
    below `min_cross_hazard_magnitude` and with no tsunami flag. Quake-to-quake pairs and records without
    hazard codes always pass."""
    for quake, other in ((a, b), (b, a)):
        if (
            is_quake(quake) and not is_quake(other) and other.hazard_codes
            and quake.magnitude is not None and quake.magnitude < rules.min_cross_hazard_magnitude and not quake.tsunami
        ):  # fmt: skip
            return False
    return True


def passes_place_and_time(seed: Record, cand: Record, rules: Rules) -> bool:
    """A shared country or a point inside the radius, and a date inside the hazard's window."""
    known_a, known_b = set(seed.countries) - {UNKNOWN_COUNTRY}, set(cand.countries) - {UNKNOWN_COUNTRY}
    if known_a and known_b and not known_a & known_b and not within_radius(seed, cand, rules):
        return False  # an unknown country never blocks: upstream sources do mislabel countries
    return gap_days(seed, cand, by_day=True) <= rules.window(seed)[0]


def bbox_around(point: tuple[float, float], km: float) -> list[float]:
    lon, lat = point
    dlat = km / 111.0
    dlon = min(180.0, km / (111.0 * max(math.cos(math.radians(lat)), 0.1)))
    return [max(-180.0, lon - dlon), max(-90.0, lat - dlat), min(180.0, lon + dlon), min(90.0, lat + dlat)]


def find_candidates(
    api: str, token: str, seed: Record, max_items: int = 60, rules: Rules = Rules()
) -> tuple[list[Record], int]:
    """(candidates, how many passed the rules before the cap): other sources' records of the seed's time and place.

    Two searches over every events collection, both limited to the hazard's time window: one by the seed's
    countries (a CQL2 `a_overlaps`), one by a box around its point (a disaster crosses borders, and a register
    holds one record per country), plus the records with the seed's `monty:corr_id`. Those are candidates like any
    other (Jev scores them), but they skip the place and time rules and come first when the list is capped.
    Records of the seed's own collection are dropped unless Montandon links them to the seed, and the episodes of
    one source event (same `monty:src_event_id`) count once.
    """
    if not seed.start:
        raise ValueError("the selected event has no date, so there is nothing to search around")
    days, radius = rules.window(seed)
    first = (seed.start - timedelta(days=days)).astimezone(UTC)
    last = ((seed.end or seed.start) + timedelta(days=days)).astimezone(UTC)
    base = {
        "collections": EVENT_COLLECTIONS, "limit": 200,
        "datetime": f"{first:%Y-%m-%dT%H:%M:%SZ}/{last:%Y-%m-%dT%H:%M:%SZ}",
    }  # fmt: skip
    found: dict[str, dict] = {}
    if seed.corr_id:  # asked for directly, so the window and the search caps never hide these records from Jev
        same = {"op": "=", "args": [{"property": "monty:corr_id"}, seed.corr_id]}
        found.update({
            f["id"]: f
            for f in _search(api, token, {"collections": EVENT_COLLECTIONS, "limit": 200, "filter-lang": "cql2-json", "filter": same})
        })  # fmt: skip
    countries = sorted(set(seed.countries) - {UNKNOWN_COUNTRY})
    if countries:
        flt = {"op": "a_overlaps", "args": [{"property": "monty:country_codes"}, countries]}
        found.update(
            {f["id"]: f for f in _search(api, token, {**base, "filter-lang": "cql2-json", "filter": flt})}
        )
    if seed.point:
        box = bbox_around(seed.point, max(radius, rules.coarse_radius_km))
        for f in _search(api, token, {**base, "bbox": box}, max_items=800):
            found.setdefault(f["id"], f)

    kept = []
    for item in found.values():
        rec = record_from_item(item)
        exact = is_linked(seed, rec)
        same_corr = bool(seed.corr_id) and rec.corr_id == seed.corr_id
        if rec.id == seed.id or (rec.source == seed.source and not exact):
            continue
        if exact or same_corr or (passes_place_and_time(seed, rec, rules) and quake_can_trigger(seed, rec, rules)):
            kept.append((not exact, not same_corr, -heuristic_score(*pair_anchor(seed, rec), rules), rec.id, rec))
    kept.sort(key=lambda x: x[:4])
    first_episode: dict[tuple[str, str], object] = {}
    unique = []
    for not_exact, _, _, _, rec in kept:
        # A source files one record per episode of an event (GDACS): keep the best-ranked episode only.
        # Records that share the id and the episode number (a register's records for several countries) stay.
        if not_exact and rec.src_event_id:
            if first_episode.setdefault((rec.source, rec.src_event_id), rec.episode) != rec.episode:
                continue
        unique.append(rec)
    return unique[:max_items], len(unique)


# -- exact links -----------------------------------------------------------------------------------------------------
# An exact link is a `related` link with the role `event` between two records that Montandon itself holds (for
# now a CEMS activation to its GDACS and Charter events). It skips Jev. A shared `monty:corr_id` does not: it only
# makes the record a candidate. Nothing is inferred from ids or numbers.
def is_linked(a: Record, b: Record) -> bool:
    """True when the catalog links one of the two records to the other, in either direction."""

    def points_at(src: Record, dst: Record) -> bool:
        # Montandon writes some link targets as the bare source id ("1474477-1630564" for the item
        # "gdacs-event-1474477-1630564"), so a target matches the item id with or without its prefix.
        names = {dst.id, dst.id.removeprefix(f"{dst.source}-event-")}
        return any(
            role == "event" and coll == f"{dst.source}-events" and iid in names
            for role, coll, iid in src.links
        )

    return points_at(a, b) or points_at(b, a)


def exact_links(seed: Record, records: list[Record]) -> dict[str, str]:
    """{record id: why} for the records Montandon links to the seed. These join without asking Jev."""
    return {rec.id: "linked in Montandon" for rec in records if rec.id != seed.id and is_linked(seed, rec)}


# -- Jev: the decision model ---------------------------------------------------------------------------------------------
KIND_LABELS = {
    "emdat": "EM-DAT disaster database record", "glide": "GLIDE disaster number record", "ifrcevent": "IFRC emergency page",
    "idmc": "IDMC displacement record", "cems": "Copernicus EMS activation", "charter": "International Charter activation",
    "usgs": "USGS earthquake catalogue entry", "gdacs": "GDACS alert", "pdc": "PDC alert", "ibtracs": "IBTrACS storm track",
    "desinventar": "DesInventar record", "gfd": "Global Flood Database event",
}  # fmt: skip
HAZARD_LABELS = {
    "EQ": "earthquake", "FL": "flood", "FF": "flash flood", "TC": "tropical cyclone", "ST": "storm", "VW": "wind storm",
    "WF": "wildfire", "LS": "landslide", "TO": "tornado", "DR": "drought", "VO": "volcanic activity", "TS": "tsunami",
    "EC": "extra-tropical cyclone", "HT": "heat wave", "CW": "cold wave", "SS": "storm surge", "AV": "avalanche",
    "EP": "epidemic", "OT": "other",
}  # fmt: skip
NOTE = (
    "The candidates are records from one source. None, one or several may describe the same disaster as the anchor: "
    "a source can hold several records of one disaster, such as an alert for each shock. Judge every candidate against "
    "the anchor and against the other candidates; a candidate that fits the anchor worse than a rival, or differs in "
    "place or date, is probably a different disaster. `days_apart` and `km_apart` were measured by the application "
    "between the anchor and that candidate; take them as given. Aftershocks minutes or hours after a main shock "
    "belong to the same disaster; a separate strong earthquake days or weeks later does not."
)


def describe(rec: Record) -> dict:
    """What the model reads about one record: words and facts, no ids and no codes."""
    out = {
        "kind": KIND_LABELS.get(rec.family, rec.family),
        "title": rec.title,
        "description": rec.text[:400],
        "hazard": HAZARD_LABELS.get(rec.glide, "unknown"),
        "starts": rec.start.date().isoformat() if rec.start else "unknown",
        "ends": (rec.end or rec.start).date().isoformat() if rec.start else "unknown",
    }
    if rec.magnitude is not None:
        out["magnitude"] = rec.magnitude
    out["countries"] = [c for c in rec.countries if c != UNKNOWN_COUNTRY] or ["unknown"]
    return out


def listwise_request(model: str, anchor: Record, candidates: list[Record], rules: Rules = Rules()) -> dict:
    """One request: the anchor, its candidates keyed by name, a yes/no per candidate and the single best one."""
    keys_ = [f"candidate_{i + 1}" for i in range(len(candidates))]
    state = {}
    for key, cand in zip(keys_, candidates, strict=True):
        _, _, gap, km = proximity(*pair_anchor(anchor, cand), rules)
        state[key] = {
            **describe(cand),
            "days_apart": int(round(gap)),
            "km_apart": "unknown" if km is None else int(round(km)),
        }
    questions: dict[str, dict] = {
        f"same_{key}": {
            "type": "noul",
            "instructions": f"Does `candidates.{key}` describe the same disaster as `anchor`? {NOTE}",
            "criteria": {
                "true": f"`candidates.{key}` is about the same physical disaster as `anchor`, whatever its date precision, location detail or wording.",
                "false": f"`candidates.{key}` is a different disaster: a separate earthquake, a later flood or storm, a different hazard, or a distant region.",
            },
        }
        for key in keys_
    }
    questions["best"] = {
        "type": "choice",
        "instructions": f"Which candidate is the best match for `anchor`, the one most likely to be the same disaster? {NOTE}",
        "criteria": {
            key: f"`candidates.{key}` ({cand.title[:60]}, {cand.start.date().isoformat() if cand.start else 'no date'}) is the best match."
            for key, cand in zip(keys_, candidates, strict=True)
        },
    }
    questions["any_match"] = {
        "type": "noul",
        "instructions": f"Does at least one candidate describe the same disaster as `anchor`? {NOTE}",
        "criteria": {
            "true": "At least one candidate is the same disaster as the anchor.",
            "false": "No candidate is the same disaster as the anchor.",
        },
    }
    return {
        "model": model,
        "state": {"anchor": describe(anchor), "candidates": state},
        "questions": questions,
    }


def call_decisions(payload: dict, api_key: str, retries: int = 3, timeout: int = 60) -> dict:
    for attempt in range(retries + 1):
        r = requests.post(
            DECISIONS_URL, json=payload, headers={"Authorization": f"Bearer {api_key}"}, timeout=timeout
        )
        if r.status_code in (429, 500, 502, 503, 524, 529) and attempt < retries:
            time.sleep(2**attempt)
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("unreachable")


@dataclass
class JevAnswer:
    scores: dict[str, float]  # candidate id -> P(same disaster as the seed)
    cost: float = 0.0  # dollars, as reported by OpenRouter
    errors: list[str] = field(default_factory=list)  # calls that failed (their candidates have no score)


def ask_jev(
    seed: Record, candidates: list[Record], api_key: str, model: str = DEFAULT_MODEL, chunk: int = 25,
    rules: Rules = Rules(),
) -> JevAnswer:  # fmt: skip
    """P(same disaster) for each candidate, from one call per source (split at `chunk` candidates).

    Candidates are grouped by source and sorted by id, so their order says nothing about the answer. With no
    key nothing is called and nothing is scored: the notebook then continues with the exact links only.
    """
    if not api_key or not candidates:
        return JevAnswer({})
    by_family: dict[str, list[Record]] = {}
    for cand in sorted(candidates, key=lambda r: r.id):
        by_family.setdefault(cand.family, []).append(cand)
    groups = [recs[i : i + chunk] for recs in by_family.values() for i in range(0, len(recs), chunk)]

    def one(group: list[Record]) -> tuple[dict[str, float], float, str]:
        try:
            response = call_decisions(listwise_request(model, seed, group, rules), api_key)
        except (
            Exception
        ) as exc:  # one failed call must not sink the run; never echo the request (it carries the key)
            return {}, 0.0, f"{type(exc).__name__}: {str(exc)[:160]}"
        answers = response.get("answers") or {}
        scores = {}
        for i, cand in enumerate(group):
            a = answers.get(f"same_candidate_{i + 1}") or {}
            if a.get("type") == "noul" and a.get("noul") is not None:
                scores[cand.id] = float(a["noul"])
        return scores, float((response.get("usage") or {}).get("cost") or 0.0), ""

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(one, groups))
    return JevAnswer(
        {k: v for scores, _, _ in results for k, v in scores.items()},
        sum(cost for _, cost, _ in results),
        [err for _, _, err in results if err],
    )


def match_table(candidates: list[Record], exact: dict[str, str], scores: dict[str, float]) -> pd.DataFrame:
    """One row per candidate, most confident first: source, title, date, P(same) and why."""
    rows = []
    for rec in candidates:
        p, why = (
            (1.0, exact[rec.id])
            if rec.id in exact
            else (scores[rec.id], "Jev")
            if rec.id in scores
            else (0.0, "not scored")
        )
        rows.append({
            "id": rec.id, "source": SOURCE_LABELS.get(rec.source, rec.source), "title": rec.title,
            "date": rec.start.date().isoformat() if rec.start else "", "p_same": p, "why": why,
        })  # fmt: skip
    cols = ["id", "source", "title", "date", "p_same", "why"]
    return (
        pd.DataFrame(rows, columns=cols)
        .sort_values("p_same", ascending=False, kind="stable")
        .reset_index(drop=True)
    )


# -- details: what each matched source says -------------------------------------------------------------------------------
# monty:impact_detail `type` -> fact-sheet field, for people only. PDC's age bands and CEMS's ancillary counts carry
# other categories, so they never inflate the people totals; "potentially_affected" is an exposure, not an impact.
PEOPLE_FIELDS = {
    "death": "deaths", "injured": "injured", "affected_total": "affected",
    "displaced_internal": "displaced", "displaced_total": "displaced",
}  # fmt: skip


def impact_field(detail: dict) -> str | None:
    kind, category = detail.get("type"), detail.get("category")
    if kind == "cost":  # PDC's `capital-total` is the capital exposed, not a loss
        return None if category == "global_currency" else "loss"
    return PEOPLE_FIELDS.get(kind) if category == "people" else None


@dataclass
class Details:
    rows: list[dict]  # source, field, value, unit, estimate_type, event
    footprints: list[dict]  # GeoJSON features: hazard footprints that are polygons


def fetch_details(api: str, token: str, events: list[Record], batch: int = 50) -> Details:
    """Follow each event's `related` links to its hazard and impact items and read the numbers off them."""
    wanted: dict[str, dict[str, str]] = {}  # collection -> {item id: event id}
    for event in events:
        for role, collection, item_id in event.links:
            if role in ("hazard", "impact"):
                wanted.setdefault(collection, {})[item_id] = event.id
    rows, footprints = [], []
    for collection, by_id in wanted.items():
        source = collection.rsplit("-", 1)[0]
        ids = list(by_id)
        for i in range(0, len(ids), batch):
            chunk = ids[i : i + batch]
            for item in _search(api, token, {"collections": [collection], "ids": chunk, "limit": len(chunk)}):
                if item["id"] not in by_id:
                    continue
                props = item.get("properties") or {}
                event = by_id[item["id"]]
                if collection.endswith("-impacts"):
                    d = props.get("monty:impact_detail") or {}
                    if (name := impact_field(d)) and d.get("value") is not None:
                        rows.append({"source": source, "field": name, "value": float(d["value"]), "unit": d.get("unit"),
                                     "estimate_type": d.get("estimate_type"), "event": event})  # fmt: skip
                elif collection.endswith("-hazards"):
                    d = props.get("monty:hazard_detail") or {}
                    if d.get("severity_value") is not None and float(d["severity_value"]) >= 0:
                        rows.append({"source": source, "field": "severity", "value": float(d["severity_value"]),
                                     "unit": d.get("severity_unit"), "estimate_type": d.get("estimate_type"), "event": event})  # fmt: skip
                    # a register's or an alert's polygon is a country outline, not a footprint
                    if source not in COARSE_SOURCES and (item.get("geometry") or {}).get("type") in (
                        "Polygon",
                        "MultiPolygon",
                    ):
                        footprints.append({"type": "Feature", "geometry": item["geometry"],
                                           "properties": {"source": source, "title": props.get("title", ""), "event": event}})  # fmt: skip
    return Details(rows, footprints)


# -- the fact sheet -------------------------------------------------------------------------------------------------------
DISAGREE_RATIO = 1.5  # sources disagree when the largest value is at least this many times the smallest
SUM_SOURCES = {
    "cems"
}  # Copernicus reports one figure per area of interest: they add up. Others: the largest value.
MAGNITUDE = "Magnitude / severity"
NUMERIC_ROWS = {
    "deaths": "Deaths", "injured": "Injured", "affected": "People affected", "displaced": "People displaced",
    "loss": "Economic loss",
}  # fmt: skip


MAGNITUDE_UNITS = {"m", "mw", "mww", "mwc", "mwb", "mb", "ml", "ms"}


def _magnitude_unit(unit: str) -> str:
    """Every kind of earthquake magnitude is "M"; any other severity (km/h, an alert score) keeps its unit."""
    return "M" if unit.lower() in MAGNITUDE_UNITS or "magnitude" in unit.lower() else unit


def _aggregate(source: str, items: list[dict], countries_of: dict[str, frozenset]) -> tuple[float, str, bool]:
    """(figure, unit, modelled) for one source and field, from its tidy rows.

    Within one record the figures are alternatives (largest value), except Copernicus areas, which add up. Across
    records, those in one country are alternatives again (one record per shock), but a register files one record
    per country (EM-DAT, GLIDE), and those add up. Records are told apart by their set of countries.
    """
    figures: dict[frozenset, list[tuple[float, dict, bool]]] = {}
    by_event: dict[str, list[dict]] = {}
    for row in items:
        by_event.setdefault(row["event"], []).append(row)
    for event, rows in by_event.items():
        if source in SUM_SOURCES:
            figure = (
                sum(r["value"] for r in rows),
                rows[0],
                any(r["estimate_type"] == "modelled" for r in rows),
            )
        else:
            top = max(rows, key=lambda r: r["value"])
            figure = (top["value"], top, top["estimate_type"] == "modelled")
        figures.setdefault(countries_of.get(event, frozenset()), []).append(figure)
    chosen = [max(group, key=lambda f: f[0]) for group in figures.values()]
    return sum(f[0] for f in chosen), chosen[0][1]["unit"] or "", any(f[2] for f in chosen)


def _whole(v: float):
    return int(v) if float(v).is_integer() else v


def _fmt(v: float, decimals: int = 0) -> str:
    return f"{v:,.{decimals}f}"


def _disagree(values: dict[str, tuple[float, str]], decimals: int = 0) -> tuple[bool, str]:
    """(do the sources disagree, range text) for {source: (value, unit key)}; only values in one unit compare."""
    groups: dict[str, list[float]] = {}
    for value, unit in values.values():
        groups.setdefault(unit, []).append(value)
    comparable = [g for g in groups.values() if len(g) > 1]
    flag = any(min(g) == 0 < max(g) or (min(g) > 0 and max(g) >= DISAGREE_RATIO * min(g)) for g in comparable)
    if not comparable:
        return False, ""
    best = max(comparable, key=len)
    lo, hi = min(best), max(best)
    return flag, _fmt(lo, decimals) if lo == hi else f"{_fmt(lo, decimals)} – {_fmt(hi, decimals)}"


def fact_sheet(records: list[Record], rows: list[dict]) -> pd.DataFrame:
    """One row per field, one column per source, then the range and whether the sources disagree.

    `records` are the matched events, `rows` the tidy details. `attrs["modelled"]` holds the (row, column) cells
    that are model estimates (USGS PAGER), `attrs["units"]` the unit each cell was published in.
    """
    countries_of = {r.id: frozenset(c for c in r.countries if c != UNKNOWN_COUNTRY) for r in records}
    cells: dict[tuple[str, str], object] = {}  # (row label, source) -> value
    units: dict[tuple[str, str], str] = {}
    modelled: set[tuple[str, str]] = set()
    by_source: dict[str, list[Record]] = {}
    for rec in records:
        by_source.setdefault(rec.source, []).append(rec)
    for source, recs in by_source.items():
        dated = [r.start for r in recs if r.start]
        if dated:
            cells[("Starts", source)] = min(dated).date().isoformat()
            cells[("Ends", source)] = max((r.end or r.start) for r in recs if r.start).date().isoformat()
        if countries := sorted({c for r in recs for c in r.countries if c != UNKNOWN_COUNTRY}):
            cells[("Countries", source)] = ", ".join(countries)
        if mags := [r.magnitude for r in recs if r.magnitude is not None]:
            cells[(MAGNITUDE, source)], units[(MAGNITUDE, source)] = max(mags), "M"

    grouped: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        grouped.setdefault((row["field"], row["source"]), []).append(row)
    for (name, source), items in grouped.items():
        if name == "severity":
            if (MAGNITUDE, source) not in cells:
                top = max(items, key=lambda r: r["value"])
                cells[(MAGNITUDE, source)], units[(MAGNITUDE, source)] = top["value"], top["unit"] or ""
            continue
        label = NUMERIC_ROWS[name]
        value, unit, is_modelled = _aggregate(source, items, countries_of)
        cells[(label, source)], units[(label, source)] = _whole(value), unit
        if is_modelled:
            modelled.add((label, source))

    order = list(SOURCE_LABELS)
    sources = sorted({s for _, s in cells}, key=lambda s: order.index(s) if s in order else len(order))
    labels = ["Starts", "Ends", "Countries", MAGNITUDE, *NUMERIC_ROWS.values()]
    table_rows, index = [], []
    for label in labels:
        present = {s: cells[(label, s)] for s in sources if (label, s) in cells}
        if not present:
            continue
        flag, span = False, ""
        if label in NUMERIC_ROWS.values():
            unit_key = (
                (lambda s, label=label: units[(label, s)]) if label == "Economic loss" else (lambda s: "")
            )
            flag, span = _disagree({s: (v, unit_key(s)) for s, v in present.items()})
        elif label == MAGNITUDE:
            _, span = _disagree(
                {s: (v, _magnitude_unit(units[(label, s)])) for s, v in present.items()}, decimals=1
            )
        table_rows.append([present.get(s) for s in sources] + [span, flag])
        index.append(label)
    column_labels = [SOURCE_LABELS.get(s, s) for s in sources]
    table = pd.DataFrame(
        table_rows, index=index, columns=[*column_labels, "Range", "Sources disagree"], dtype=object
    )
    name_of = dict(zip(sources, column_labels, strict=True))
    table.attrs["modelled"] = {(label, name_of[s]) for label, s in modelled}
    table.attrs["units"] = {(label, name_of[s]): u for (label, s), u in units.items()}
    return table


def display_table(facts: pd.DataFrame):
    """The fact sheet as a pandas Styler: formatted numbers, modelled ones marked with *, disagreeing rows highlighted."""
    modelled, units = facts.attrs.get("modelled", set()), facts.attrs.get("units", {})
    sources = [c for c in facts.columns if c not in ("Range", "Sources disagree")]

    def text(label: str, col: str, value) -> str:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return ""
        if label in ("Starts", "Ends"):
            return datetime.fromisoformat(value).strftime("%d\u00a0%b\u00a0%y")  # short, and never wrapped
        if isinstance(value, str):
            return value
        unit = units.get((label, col), "")
        out = f"{value:,.1f}" if label == MAGNITUDE else f"{value:,.0f}"
        if (
            label == MAGNITUDE
            and unit
            and "magnitude" not in unit.lower()
            and unit.lower() not in MAGNITUDE_UNITS
        ):
            out += f" {unit}"  # a severity in km/h or an alert score keeps its unit; a magnitude is just a number
        elif label == "Economic loss" and unit:
            out += f" {unit}"
        return out + ("*" if (label, col) in modelled else "")

    shown = pd.DataFrame(
        {c: [text(r, c, facts.loc[r, c]) for r in facts.index] for c in sources}, index=facts.index
    )
    shown["Range"] = facts["Range"]
    disagree = [bool(v) for v in facts["Sources disagree"]]

    def paint(frame: pd.DataFrame) -> pd.DataFrame:
        css = pd.DataFrame("", index=frame.index, columns=frame.columns)
        for row, flagged in zip(frame.index, disagree, strict=True):
            for col in sources:
                if flagged and frame.loc[row, col]:
                    css.loc[row, col] = "background-color: #fbd9b8; color: #3a1d00; font-weight: 600"
        return css

    return shown.style.apply(paint, axis=None).set_table_styles(
        [{"selector": "td, th", "props": "font-size: 0.82rem; padding: 4px 6px;"}]
    )


def map_view(points: list[tuple[float, float]]) -> dict:
    """A lonboard view state (centre and zoom) that frames the points, for a map about 700 px wide."""
    lons, lats = [p[0] for p in points], [p[1] for p in points]
    span = max(max(lons) - min(lons), max(lats) - min(lats), 0.01)
    zoom = math.log2(492 / (span * 1.4))  # at zoom z, 700 px show about 492 / 2**z degrees
    return {"longitude": (max(lons) + min(lons)) / 2, "latitude": (max(lats) + min(lats)) / 2,
            "zoom": round(min(8.0, max(1.0, zoom)), 1)}  # fmt: skip


COLOR_BY_LABEL = {SOURCE_LABELS[k]: v for k, v in SOURCE_COLORS.items()}


def footprints(events: list[Record], details: Details) -> list[dict]:
    """Precise footprints as GeoJSON features: hazard polygons and events with their own polygon (not country outlines)."""
    own = [
        {"type": "Feature", "geometry": r.geometry,
         "properties": {"source": r.source, "title": r.title, "event": r.id}}
        for r in events
        if not r.coarse and (r.geometry or {}).get("type") in ("Polygon", "MultiPolygon")
    ]  # fmt: skip
    return own + details.footprints
