"""
Disasters, worked out on demand from any of their IDs.

No source ID ties a disaster together in Montandon: for one earthquake, USGS, PDC
and EM-DAT may share a monty:corr_id while GLIDE, IFRC GO, IDMC and CEMS each
carry another, and GDACS puts its episode id in its corr_id. So a disaster is
built around a seed item:

1. find the seed: a stored StacItem with the ref, or a live lookup by source id;
2. search the event collections for the same hazard, in the same countries,
   within a hazard-dependent time window;
3. score each candidate against the seed: shared refs (corr_id stem, explicit
   cross-source links, the same source id) count as certain; otherwise time gap
   and distance decide. Country-level sources (GLIDE, EM-DAT, IFRC GO, IDMC…)
   only give a date and a rough point, so they get day precision and a wide radius;
4. re-anchor on the strongest precise member (the largest magnitude) and score
   again, so every ID of a disaster lands on the same group;
5. apply staff corrections (EventLinkOverride).

Results are cached; corrections invalidate the cache. Analyses belong to a
disaster when their refs (own or via their STAC item) overlap the disaster's.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from django.core.cache import cache
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from . import refs as R
from .models import CatalogProfile, EventLinkOverride, LinkVerdict, StacCatalog, StacItem
from .stac_client import StacClientError, catalog_collections

log = logging.getLogger(__name__)

STRONG = 0.7  # joins the disaster
POSSIBLE = 0.4  # shown as "possibly the same disaster"
CACHE_SECONDS = 3600
MAX_CANDIDATES = 400

# GLIDE hazard code → (time window in days, radius in km) for matching precise items.
HAZARD_WINDOWS = {
    "EQ": (7, 300),
    "TS": (7, 1500),
    "VO": (30, 200),
    "LS": (7, 100),
    "AV": (7, 100),
    "FL": (30, 500),
    "FF": (14, 300),
    "TC": (21, 2500),
    "ST": (14, 800),
    "SS": (14, 800),
    "DR": (180, 2000),
    "WF": (30, 300),
    "HT": (30, 1500),
    "CW": (30, 1500),
    "EP": (90, 3000),
}
DEFAULT_WINDOW = (14, 500)
# Sources that only locate an event at country level and date it to the day.
COARSE_SOURCES = {"glide", "emdat", "ifrcevent", "idmc-idu", "idmc-gidd", "desinventar"}
COARSE_RADIUS_KM = 1500
# Which source names the disaster best, in order; otherwise the anchor's title.
TITLE_SOURCES = ["ifrcevent", "cems", "usgs", "gdacs", "pdc", "emdat", "glide"]
# Which precise source anchors a disaster when magnitudes tie.
ANCHOR_SOURCES = ["usgs", "gdacs", "pdc"]
UNDRR_RE = re.compile(r"^[A-Z]{2}\d{4}$")
PDC_MAGNITUDE_RE = re.compile(r"Earthquake.*? - (\d+(?:\.\d+)?) - ")


class DisasterError(RuntimeError):
    pass


@dataclass
class Member:
    """One source's item in a disaster."""

    item: dict
    collection: str
    item_id: str
    source: str
    ref: str
    refs: list[str]
    title: str
    start: datetime | None
    end: datetime | None
    countries: list[str]
    hazard_codes: list[str]
    point: tuple[float, float] | None
    coarse: bool
    magnitude: float | None
    stac_url: str = ""
    score: float = 0.0
    reason: str = ""
    override: str = ""

    @property
    def glide_code(self) -> str:
        return next((c for c in self.hazard_codes if len(c) == 2 and c.isalpha()), "")

    @property
    def undrr_code(self) -> str:
        return next((c for c in self.hazard_codes if UNDRR_RE.match(c)), "")

    @property
    def source_label(self) -> str:
        return R.label(R.ref(self.source, "")).strip()

    @property
    def link(self) -> str:
        """A public page for the item: its source's site when known, else the STAC item."""
        return R.external_url(self.ref) or _html_via(self.item) or self.stac_url


@dataclass
class SourceGroup:
    source: str
    label: str
    members: list[Member]

    @property
    def primary(self) -> Member:
        return self.members[0]

    @property
    def others(self) -> list[Member]:
        return self.members[1:]


@dataclass
class Disaster:
    key: str
    catalog_id: int | None
    title: str
    anchor: Member
    members: list[Member]
    possible: list[Member] = field(default_factory=list)
    removed: list[Member] = field(default_factory=list)
    error: str = ""

    @property
    def refs(self) -> list[str]:
        return sorted({r for m in self.members for r in m.refs})

    @property
    def id_refs(self) -> list[str]:
        """Every source id among the members (not stac / corr), short forms dropped."""
        return _id_refs(r for m in self.members for r in m.refs)

    @property
    def headline_refs(self) -> list[str]:
        """The IDs worth showing: each source's primary item, plus sources only known through links."""
        grouped = {g.source for g in self.groups}
        refs = [r for g in self.groups for r in g.primary.refs]
        refs += [r for r in self.id_refs if R.split(r)[0] not in grouped]
        return _id_refs(refs)

    @property
    def countries(self) -> list[str]:
        return sorted({c for m in self.members for c in m.countries})

    @property
    def start(self) -> datetime | None:
        return min((m.start for m in self.members if m.start), default=None)

    @property
    def end(self) -> datetime | None:
        return max((m.end or m.start for m in self.members if m.start), default=None)

    @property
    def hazard_codes(self) -> list[str]:
        return self.anchor.hazard_codes

    @property
    def groups(self) -> list[SourceGroup]:
        by_source: dict[str, list[Member]] = {}
        for m in sorted(
            self.members, key=lambda m: (-m.score, -(m.magnitude or 0), m.start or timezone.now())
        ):
            by_source.setdefault(m.source, []).append(m)
        order = {s: i for i, s in enumerate(TITLE_SOURCES)}
        return [
            SourceGroup(s, ms[0].source_label, ms)
            for s, ms in sorted(by_source.items(), key=lambda kv: (order.get(kv[0], 99), kv[0]))
        ]

    @property
    def geojson(self) -> dict:
        features = []
        for m in self.members:
            geom = m.item.get("geometry")
            if geom:
                features.append(
                    {
                        "type": "Feature",
                        "geometry": geom,
                        "properties": {"source": m.source_label, "title": m.title, "coarse": m.coarse},
                    }
                )
        return {"type": "FeatureCollection", "features": features}


def _id_refs(refs) -> list[str]:
    out = {r for r in refs if R.split(r)[0] not in {"stac", "corr", "corrstem"}}
    # glide:EQ-2025-000043 and emdat:2025-0223 are short forms of the -MMR refs next to them
    out = {r for r in out if not any(o != r and o.startswith(r + "-") for o in out)}
    order = {s: i for i, s in enumerate(TITLE_SOURCES)}
    return sorted(out, key=lambda r: (order.get(R.split(r)[0], 99), r))


# -- building members ---------------------------------------------------------
def _dt(value) -> datetime | None:
    dt = parse_datetime(str(value)) if value else None
    if dt and dt.tzinfo is None:
        dt = timezone.make_aware(dt, timezone.utc)
    return dt


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
    if not points:
        return None
    return sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points)


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


def _html_via(item: dict) -> str:
    for link in item.get("links") or []:
        if link.get("rel") == "via" and link.get("type") == "text/html":
            return link.get("href", "")
    return ""


def member_from_item(item: dict, catalog: StacCatalog | None = None) -> Member:
    props = item.get("properties") or {}
    collection = item.get("collection") or ""
    source = R.source_of(collection)
    monty = catalog is None or catalog.is_montandon
    start = _dt(props.get("start_datetime") or props.get("datetime"))
    point = _centroid(item.get("geometry"))
    return Member(
        item=item,
        collection=collection,
        item_id=item.get("id", ""),
        source=source,
        ref=R.primary_ref(item, monty=monty),
        refs=R.item_refs(item, monty=monty),
        title=props.get("title") or item.get("id", ""),
        start=start,
        end=_dt(props.get("end_datetime")) or start,
        countries=[c.upper() for c in props.get("monty:country_codes") or []],
        hazard_codes=list(props.get("monty:hazard_codes") or []),
        point=point,
        coarse=source in COARSE_SOURCES or point is None,
        magnitude=_magnitude(source, props),
        stac_url=catalog.item_url(collection, item.get("id", "")) if catalog else "",
    )


# -- scoring --------------------------------------------------------------------
def _km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lon1, lat1, lon2, lat2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def window(member: Member) -> tuple[int, int]:
    return HAZARD_WINDOWS.get(member.glide_code, DEFAULT_WINDOW)


def _gap_days(a: Member, b: Member, by_day: bool) -> float:
    if not (a.start and b.start):
        return 0.0
    a0, a1, b0, b1 = a.start, a.end or a.start, b.start, b.end or b.start
    if by_day:
        a0, a1, b0, b1 = (d.date() for d in (a0, a1, b0, b1))
        gap = max(b0 - a1, a0 - b1, timedelta(0))
        return gap.days
    gap = max(b0 - a1, a0 - b1, timedelta(0))
    return gap.total_seconds() / 86400


def score(anchor: Member, cand: Member) -> tuple[float, str]:
    """How sure we are that `cand` is the same disaster as `anchor` (0–1) and why."""
    if cand.ref == anchor.ref:
        return 1.0, "anchor"
    shared = (set(anchor.refs) & set(cand.refs)) - {
        r for r in anchor.refs if r.startswith(("stac:", "corr:"))
    }
    if any(r.startswith("corrstem:") for r in shared):
        return 1.0, "same monty:corr_id"
    if shared:
        return 1.0, f"linked by {R.label(sorted(shared)[0])}"
    days, radius = window(anchor)
    coarse = anchor.coarse or cand.coarse
    gap = _gap_days(anchor, cand, by_day=coarse)
    t = max(0.0, 1 - gap / days)
    if anchor.point and cand.point:
        km = _km(anchor.point, cand.point)
        d = max(0.0, 1 - km / (max(radius, COARSE_RADIUS_KM) if coarse else radius))
        where = f"{km:.0f} km"
    else:
        d, where = 0.5, "location unknown"
    s = (t + d) / 2
    if not (t and d):
        s = min(s, POSSIBLE - 0.1)  # right place at the wrong time (or the reverse) is not a match
    if anchor.undrr_code and cand.undrr_code and anchor.undrr_code != cand.undrr_code:
        s *= 0.9
    return round(s, 2), f"{gap:.0f} d apart, {where}"


def _pick_anchor(seed: Member, strong: list[Member]) -> Member:
    """The largest-magnitude precise member, so every ID of a disaster re-centres on the same item."""
    precise = [m for m in [seed, *strong] if not m.coarse and m.magnitude is not None]
    if not precise:
        return seed
    rank = {s: i for i, s in enumerate(ANCHOR_SOURCES)}
    return max(
        precise,
        key=lambda m: (m.magnitude, -rank.get(m.source, 99), -(m.start or timezone.now()).timestamp()),
    )


def cluster(seed: Member, candidates: list[Member], overrides: dict[str, str] | None = None):
    """(anchor, members, possible, removed) for a seed and candidate items."""
    pool = {m.ref: m for m in [seed, *candidates] if m.ref}

    def rank(anchor: Member):
        members, possible = [], []
        for m in pool.values():
            m.score, m.reason = score(anchor, m)
            (members if m.score >= STRONG else possible if m.score >= POSSIBLE else []).append(m)
        return members, possible

    members, _ = rank(seed)
    anchor = _pick_anchor(seed, members)
    members, possible = rank(anchor)
    removed = []
    for verdict_ref, verdict in (overrides or {}).items():
        m = pool.get(verdict_ref)
        if m is None:
            continue
        m.override = verdict
        if verdict == LinkVerdict.DIFFERENT and m is not anchor:
            members = [x for x in members if x is not m]
            possible = [x for x in possible if x is not m]
            removed.append(m)
        elif verdict == LinkVerdict.SAME and m not in members:
            possible = [x for x in possible if x is not m]
            m.score, m.reason = 1.0, "confirmed by staff"
            members.append(m)
    return anchor, members, sorted(possible, key=lambda m: -m.score), removed


# -- resolving -----------------------------------------------------------------
def default_catalog() -> StacCatalog | None:
    qs = StacCatalog.objects.filter(is_active=True)
    return qs.filter(profile=CatalogProfile.MONTANDON).first() or qs.first()


def _event_collections(catalog: StacCatalog) -> list[str]:
    return [c["id"] for c in catalog_collections(catalog) if c["id"].endswith("-events")]


def _first(iterator) -> dict | None:
    return next(iter(iterator), None)


def _src_filter(value: str) -> dict:
    return {"op": "=", "args": [{"property": "monty:src_event_id"}, value]}


def _get(client, collection: str, item_id: str) -> dict | None:
    try:
        return client.get_item(collection, item_id)
    except StacClientError as exc:
        if "-> 404" in str(exc):
            return None
        raise


def _search_one(client, collections: list[str], flt: dict) -> dict | None:
    """First item matching a property filter. Montandon answers 500 when such a query times out
    on a big collection (e.g. monty:src_event_id on usgs-events); that counts as not found."""
    try:
        return _first(client.search(collections, filter=flt, limit=1, max_items=1, sortby_field=None))
    except StacClientError as exc:
        if re.search(r"-> 5\d\d", str(exc)):
            log.info("lookup %s in %s failed: %s", flt, collections, exc)
            return None
        raise


def _fetch_seed(client, r: str, event_collections: list[str]) -> dict | None:
    """Look an item up live by one ref: by its predictable item id when possible, else by property."""
    scheme, value = R.split(r)
    if scheme == "stac":
        collection, _, item_id = value.partition("/")
        return _get(client, collection, item_id)
    if scheme == "glide":
        if value.count("-") == 3:
            return _get(client, "glide-events", f"glide-event-{value}")
        return _search_one(client, ["glide-events"], _src_filter(value.split("-", 1)[1]))
    if scheme == "emdat":
        return _get(client, "emdat-events", f"emdat-event-{value}") if value.count("-") == 2 else None
    if scheme == "corr":
        corr = {"op": "=", "args": [{"property": "monty:corr_id"}, value]}
        return _search_one(client, event_collections, corr)
    collection = f"{scheme}-events"
    if scheme == "corrstem" or collection not in event_collections:
        return None
    # <source>-events/<source>-event-<id> holds for usgs, ifrcevent, cems, idmc…; not for gdacs or pdc
    return _get(client, collection, f"{scheme}-event-{value}") or _search_one(
        client, [collection], _src_filter(value)
    )


def _window_search(client, seed: Member, event_collections: list[str]) -> list[dict]:
    days, _ = window(seed)
    if not seed.start:
        return []
    args = []
    if seed.countries:
        args.append({"op": "a_overlaps", "args": [{"property": "monty:country_codes"}, seed.countries]})
    if seed.hazard_codes:
        args.append({"op": "a_overlaps", "args": [{"property": "monty:hazard_codes"}, seed.hazard_codes]})
    if not args:
        return []
    return list(
        client.search(
            event_collections,
            since=seed.start - timedelta(days=days),
            until=(seed.end or seed.start) + timedelta(days=days),
            filter={"op": "and", "args": args} if len(args) > 1 else args[0],
            limit=100,
            max_items=MAX_CANDIDATES,
        )
    )


def _overrides(catalog: StacCatalog, disaster_refs: set[str]) -> dict[str, str]:
    rows = EventLinkOverride.objects.filter(catalog=catalog, disaster_ref__in=disaster_refs)
    return {o.item_ref: o.verdict for o in rows}


def _local_items(refs: list[str]):
    return StacItem.objects.select_related("catalog").filter(event_refs__overlap=refs)


def _build_live(catalog: StacCatalog, refs: list[str]) -> Disaster | None:
    local = _local_items(refs).filter(catalog=catalog).first()
    with catalog.client() as client:
        event_collections = _event_collections(catalog)
        raw = local.raw if local and local.raw else None
        if raw is None:
            for r in refs:
                raw = _fetch_seed(client, r, event_collections)
                if raw:
                    break
        if raw is None:
            return None
        raw = {**raw, "collection": raw.get("collection") or (local.collection if local else "")}
        seed = member_from_item(raw, catalog)
        candidates = [member_from_item(i, catalog) for i in _window_search(client, seed, event_collections)]
    anchor, members, _, _ = cluster(seed, candidates)
    group_refs = {r for m in members for r in m.refs} | {anchor.ref}
    anchor, members, possible, removed = cluster(seed, candidates, _overrides(catalog, group_refs))
    return _disaster(catalog, anchor, members, possible, removed)


def _build_local(refs: list[str], error: str = "") -> Disaster | None:
    """From stored items only (the catalog is unreachable or not Montandon)."""
    items = list(_local_items(refs))
    if not items:
        return None
    stems = [r for i in items for r in i.event_refs if r.startswith("corrstem:")]
    if stems:
        items += [i for i in _local_items(stems) if i not in items]
    members = []
    for i in items:
        m = member_from_item(
            {**(i.raw or i.as_stac_item()), "collection": i.collection, "id": i.item_id}, i.catalog
        )
        m.refs = list(dict.fromkeys([*m.refs, *i.event_refs]))
        m.score, m.reason = 1.0, "stored item"
        members.append(m)
    anchor = _pick_anchor(members[0], members[1:])
    return _disaster(items[0].catalog, anchor, members, error=error)


def _disaster(catalog, anchor, members, possible=(), removed=(), error="") -> Disaster:
    best: dict[str, Member] = {}
    for m in sorted(members, key=lambda m: (m.score, m.magnitude or 0), reverse=True):
        best.setdefault(m.source, m)
    best[anchor.source] = anchor
    title = next((best[s].title for s in TITLE_SOURCES if s in best), anchor.title)
    return Disaster(
        key=anchor.ref,
        catalog_id=catalog.pk if catalog else None,
        title=title,
        anchor=anchor,
        members=members,
        possible=list(possible),
        removed=list(removed),
        error=error,
    )


def _generation() -> int:
    return cache.get_or_set("disaster:gen", 1, None)


def invalidate() -> None:
    """Drop every cached disaster (after a staff correction)."""
    try:
        cache.incr("disaster:gen")
    except ValueError:
        cache.set("disaster:gen", 2, None)


def resolve(text: str, catalog: StacCatalog | None = None, *, live: bool = True) -> Disaster | None:
    """The disaster named by an ID the user typed (any form `refs.parse` accepts), or None."""
    refs = R.parse(text)
    if not refs:
        return None
    catalog = catalog or default_catalog()
    key = f"disaster:{_generation()}:{catalog.pk if catalog else 0}:{int(live)}:{'|'.join(refs)}"
    cached = cache.get(key)
    if cached is not None:
        return cached
    disaster = None
    if live and catalog is not None and catalog.is_montandon:
        try:
            disaster = _build_live(catalog, refs)
        except StacClientError as exc:
            log.warning("disaster lookup for %s failed: %s", text, exc)
            disaster = _build_local(
                refs, error=f"{catalog} could not be reached ({exc}); showing stored items only."
            )
            if disaster is None:
                raise DisasterError(f"{catalog} could not be reached: {exc}") from exc
            return disaster  # not cached: try the catalog again next time
    if disaster is None:
        disaster = _build_local(refs)
    if disaster is not None:
        cache.set(key, disaster, CACHE_SECONDS)
    return disaster


# -- analyses -----------------------------------------------------------------
def runs_for(disaster: Disaster, qs=None):
    from factory.notebooks.models import NotebookRun

    qs = NotebookRun.objects.all() if qs is None else qs
    refs = disaster.refs
    return qs.filter(Q(event_refs__overlap=refs) | Q(stac_item__event_refs__overlap=refs)).distinct()


def possibly_related_runs(disaster: Disaster, limit: int = 24):
    """Analyses without a disaster link in the same countries and hazard, made around the disaster's time."""
    from factory.catalog import facets
    from factory.notebooks.models import NotebookRun

    if not disaster.countries or not disaster.start:
        return NotebookRun.objects.none()
    days, _ = window(disaster.anchor)
    in_countries = Q(area__country_id__in=disaster.countries)
    for iso3 in disaster.countries:
        in_countries |= Q(stac_item__country_codes__contains=[iso3])
    qs = NotebookRun.objects.with_related().filter(
        in_countries,
        created_at__gte=disaster.start - timedelta(days=3),
        created_at__lte=(disaster.end or disaster.start) + timedelta(days=max(days, 30) * 6),
    )
    hazard_options = facets.for_stac_item(disaster.anchor.item)
    if hazard_options:
        qs = qs.filter(facet_options__in=hazard_options)
    return qs.exclude(pk__in=runs_for(disaster).values("pk")).distinct()[:limit]


def link_refs(disaster: Disaster) -> list[str]:
    """What a run linked by hand to the disaster stores: its key plus the GLIDE number, when there is one."""
    glide = [r for r in disaster.id_refs if r.startswith("glide:")]
    return list(dict.fromkeys([disaster.key, *glide]))
