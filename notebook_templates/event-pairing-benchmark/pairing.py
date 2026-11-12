"""Helpers for the event-pairing benchmark: which records are the same real-world disaster?

Pure Python + numpy (no Django). Network access is limited to `fetch_items` (the Montandon STAC API)
and `score_with_model` (the OpenRouter Decisions API).

`heuristic_score` mirrors `factory/events/disasters.py::score` minus its shared-ID shortcuts, so the
benchmark measures that matcher, which is an experiment in notebook-factory and not an authoritative
Montandon method. `tests/test_event_pairing_benchmark.py` keeps the two
in step: change one and that test tells you to change the other.
"""

from __future__ import annotations

import html
import math
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import combinations

import numpy as np
import requests

# -- constants mirrored from factory/events/disasters.py -------------------------------------------
STRONG = 0.7  # notebook-factory's "joins the disaster" threshold
POSSIBLE = 0.4  # notebook-factory's "possibly the same disaster" threshold
# GLIDE hazard code -> (time window in days, radius in km)
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
UNDRR_RE = re.compile(r"^[A-Z]{2}\d{4}$")
PDC_MAGNITUDE_RE = re.compile(r"Earthquake.*? - (\d+(?:\.\d+)?) - ")

# -- constants of this benchmark -------------------------------------------------------------------
# IDMC publishes IDU and GIDD under one record id; pairing them says nothing, so they are one family.
FAMILIES = {"idmc-idu": "idmc", "idmc-gidd": "idmc"}
# Hazard clusters (first four characters of a UNDRR-ISC 2025 code) that routinely occur together. A
# hand-set grouping for this benchmark, not part of the taxonomy: MH01 convective, MH03 wind and
# pressure (cyclones), MH04 precipitation, MH06 water (floods), MH07 marine; GH01 seismic, GH02
# volcanic, GH03 ground failure.
CASCADE_GROUPS = ({"MH01", "MH03", "MH04", "MH06", "MH07"}, {"GH01", "GH02", "GH03", "MH07"})
UNKNOWN_COUNTRY = "UNK"

_PREFIXES = (
    "emdat", "glide", "ifrcevent", "idmc-idu", "idmc-gidd", "cems", "charter",
    "usgs", "gdacs", "pdc", "desinventar", "gfd",
)  # fmt: skip


# -- records -----------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Record:
    """One Montandon event item, reduced to what pairing looks at."""

    id: str
    source: str
    title: str
    text: str
    start: datetime | None
    end: datetime | None
    countries: tuple[str, ...]
    hazard_codes: tuple[str, ...]
    point: tuple[float, float] | None  # (lon, lat)
    coarse: bool
    magnitude: float | None
    corr_id: str = ""

    @property
    def family(self) -> str:
        return FAMILIES.get(self.source, self.source)

    @property
    def glide(self) -> str:
        return next((c for c in self.hazard_codes if len(c) == 2 and c.isalpha()), "")

    @property
    def undrr(self) -> str:
        return next((c for c in self.hazard_codes if UNDRR_RE.match(c)), "")

    @property
    def cluster(self) -> str:
        return self.undrr[:4]


def collection_of(item_id: str) -> str:
    """The events collection an item id lives in (IBTrACS ids are bare storm ids)."""
    for prefix in _PREFIXES:
        if item_id.startswith(f"{prefix}-event-"):
            return f"{prefix}-events"
    return "ibtracs-events"


def _dt(value) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(str(value))
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


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


def clean_text(value: str | None, limit: int = 600) -> str:
    """Plain text from a description: no HTML, markdown images or URLs; whitespace collapsed."""
    s = re.sub(r"!\[[^\]]*\]\([^)]*\)|https?://\S+|<[^>]+>", " ", html.unescape(value or ""))
    return re.sub(r"\s+", " ", s).strip()[:limit]


def record_from_item(item: dict) -> Record:
    props = item.get("properties") or {}
    collection = item.get("collection") or collection_of(item.get("id", ""))
    source = collection.removesuffix("-events")
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
        corr_id=props.get("monty:corr_id") or "",
    )


def fetch_items(api_url: str, token: str, ids: list[str], timeout: int = 90) -> dict[str, dict]:
    """{id: STAC item} for the ids that exist in the Montandon catalog."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    by_collection: dict[str, list[str]] = {}
    for item_id in ids:
        by_collection.setdefault(collection_of(item_id), []).append(item_id)
    found: dict[str, dict] = {}
    for collection, wanted in by_collection.items():
        for i in range(0, len(wanted), 20):
            r = requests.post(
                f"{api_url.rstrip('/')}/search",
                json={"collections": [collection], "ids": wanted[i : i + 20], "limit": 50},
                headers=headers,
                timeout=timeout,
            )
            r.raise_for_status()
            for feature in r.json().get("features", []):
                found[feature["id"]] = feature
    return found


# -- notebook-factory's experimental heuristic -----------------------------------------------------------------------------
def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lon1, lat1, lon2, lat2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def window(rec: Record) -> tuple[int, int]:
    return HAZARD_WINDOWS.get(rec.glide, DEFAULT_WINDOW)


def gap_days(a: Record, b: Record, by_day: bool) -> float:
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


def proximity(anchor: Record, cand: Record) -> tuple[float, float, float, float | None]:
    """(time score, distance score, gap in days, km): the two terms notebook-factory's score averages."""
    days, radius = window(anchor)
    coarse = anchor.coarse or cand.coarse
    gap = gap_days(anchor, cand, by_day=coarse)
    t = max(0.0, 1 - gap / days)
    if anchor.point and cand.point:
        km = haversine_km(anchor.point, cand.point)
        d = max(0.0, 1 - km / (max(radius, COARSE_RADIUS_KM) if coarse else radius))
        return t, d, gap, km
    return t, 0.5, gap, None


def heuristic_score(anchor: Record, cand: Record) -> float:
    """notebook-factory's score for a pair with no shared source ID: time and distance, hazard-code penalty."""
    t, d, _, _ = proximity(anchor, cand)
    s = (t + d) / 2
    if not (t and d):
        s = min(s, POSSIBLE - 0.1)  # right place at the wrong time (or the reverse) is not a match
    if anchor.undrr and cand.undrr and anchor.undrr != cand.undrr:
        s *= 0.9
    return round(s, 2)


# -- stage 1: candidates ---------------------------------------------------------------------------------
def within_radius(a: Record, b: Record) -> bool:
    """The two records' points are within the pair's radius (the anchor's hazard radius; 1,500 km at least
    when one record is only located at country level). False when either has no point."""
    anchor, cand = pair_anchor(a, b)
    km = proximity(anchor, cand)[3]
    if km is None:
        return False
    radius = window(anchor)[1]
    return km <= (max(radius, COARSE_RADIUS_KM) if anchor.coarse or cand.coarse else radius)


def block(a: Record, b: Record, block_days: float) -> str:
    """ "ok" when the pair is a candidate, else the reason it is not: "country" or "time".

    Records naming different countries are still candidates when they lie within the pair's radius: a
    cyclone or an earthquake crosses borders, and one disaster has a record per country in EM-DAT and GLIDE.
    An unknown country (UNK or none) never blocks: upstream sources do mislabel countries.
    """
    ca, cb = set(a.countries) - {UNKNOWN_COUNTRY}, set(b.countries) - {UNKNOWN_COUNTRY}
    if ca and cb and not ca & cb and not within_radius(a, b):
        return "country"
    if gap_days(a, b, by_day=True) > block_days:
        return "time"
    return "ok"


# -- the other matching rules used across Montandon ---------------------------------------------------------
_CORR_KEY = re.compile(r"^(\d{8})-([A-Z]{3})-")


def corr_id_equal(a: Record, b: Record) -> bool:
    """monty:corr_id equality: how the skills' get_event_detail follows an event across sources."""
    return bool(a.corr_id) and a.corr_id == b.corr_id


def event_key_equal(a: Record, b: Record) -> bool:
    """Same date and country in the corr_id: the MCP toolset's `event_key` blocking key."""
    ka, kb = _CORR_KEY.match(a.corr_id), _CORR_KEY.match(b.corr_id)
    return bool(ka and kb and ka.groups() == kb.groups())


def cql2_match(a: Record, b: Record, days: int) -> bool:
    """The extension docs' Algorithm 1: a shared hazard code, a shared country, intersecting dates."""
    if not (a.start and b.start):
        return False
    slack = timedelta(days=days)
    return bool(
        set(a.hazard_codes) & set(b.hazard_codes)
        and set(a.countries) & set(b.countries)
        and a.start - slack <= (b.end or b.start)
        and b.start <= (a.end or a.start) + slack
    )


RULES = {
    "Rule: corr_id equal": corr_id_equal,
    "Rule: date + country": event_key_equal,
    "Rule: CQL2 hazard + country + 1 day": lambda a, b: cql2_match(a, b, 1),
    "Rule: CQL2 hazard + country + 7 days": lambda a, b: cql2_match(a, b, 7),
}


# -- features for the learned scorer -----------------------------------------------------------------------
_STOP = frozenset(
    """that this with from were have been into over near after during about their there which
    earthquake earthquakes flood floods flooding wildfire wildfires storm storms cyclone tropical
    event disaster ground movement magnitude province region district city report reported according
    humanitarian impact medium high alert green orange red""".split()
)


def tokens(rec: Record) -> set[str]:
    """Distinctive words (mostly place names) from the title and the start of the description."""
    ascii_text = (
        unicodedata.normalize("NFKD", f"{rec.title} {rec.text[:400]}").encode("ascii", "ignore").decode()
    )
    return set(re.findall(r"[a-z]{4,}", ascii_text.lower())) - _STOP


def hazard_relation(a: Record, b: Record) -> str:
    """same | cluster | cascade | different | unknown, from the UNDRR-ISC 2025 codes."""
    if not (a.undrr and b.undrr):
        return "unknown"
    if a.undrr == b.undrr:
        return "same"
    if a.cluster == b.cluster:
        return "cluster"
    if any(a.cluster in g and b.cluster in g for g in CASCADE_GROUPS):
        return "cascade"
    return "different"


FEATURES = (
    "time", "gap_log", "near", "no_geo", "time_x_near", "one_coarse", "both_coarse",
    "same_undrr", "same_cluster", "cascade", "text_overlap", "mag_known", "mag_diff",
)  # fmt: skip


def pair_features(a: Record, b: Record) -> dict[str, float]:
    anchor, cand = pair_anchor(a, b)
    t, d, gap, km = proximity(anchor, cand)
    rel = hazard_relation(a, b)
    ta, tb = tokens(a), tokens(b)
    both_mag = a.magnitude is not None and b.magnitude is not None
    return {
        "time": t,
        "gap_log": math.log1p(gap),
        "near": d,
        "no_geo": float(km is None),
        "time_x_near": t * d,
        "one_coarse": float(a.coarse != b.coarse),
        "both_coarse": float(a.coarse and b.coarse),
        "same_undrr": float(rel == "same"),
        "same_cluster": float(rel in ("same", "cluster")),
        "cascade": float(rel == "cascade"),
        "text_overlap": len(ta & tb) / min(len(ta), len(tb)) if ta and tb else 0.0,
        "mag_known": float(both_mag),
        "mag_diff": min(abs(a.magnitude - b.magnitude), 2.0) / 2.0 if both_mag else 0.0,
    }


# -- gold labels and pairs -------------------------------------------------------------------------------------
def gold_labels(gold: dict) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """({item id: disaster id}, {disaster id: window}, {disaster id: kind}) from gold_disasters.yml."""
    label, windows, kinds = {}, {}, {}
    for d in gold["disasters"]:
        windows[d["id"]], kinds[d["id"]] = d["window"], d.get("kind", "")
        for item_id in d["members"]:
            label[item_id] = d["id"]
    return label, windows, kinds


def build_pairs(records: dict[str, Record], gold: dict, block_days: float) -> list[dict]:
    """Every cross-source pair inside a window, with its label, blocking verdict and features.

    Positive: same disaster. Negative: different disasters of the same window. Pairs inside one
    source family are skipped (a source does not need pairing with itself).
    """
    label, windows, kinds = gold_labels(gold)
    rows = []
    for ida, idb in combinations(sorted(records), 2):
        a, b = records[ida], records[idb]
        da, db = label[ida], label[idb]
        if a.family == b.family or windows[da] != windows[db]:
            continue
        verdict = block(a, b, block_days)
        anchor, cand = pair_anchor(a, b)
        row = {
            "a": ida,
            "b": idb,
            "y": int(da == db),
            "window": windows[da],
            "disaster": da if da == db else f"{da} | {db}",
            "kind": kinds[da] if da == db else "",
            "block": verdict,
            "source_a": a.family,
            "source_b": b.family,
            "pair_type": ("coarse-coarse", "coarse-precise", "precise-precise")[
                2 - int(a.coarse) - int(b.coarse)
            ],
            "hazard_rel": hazard_relation(a, b),
            "heuristic": None,
        }
        if verdict == "ok":
            row["heuristic"] = heuristic_score(anchor, cand)
            row["gap_days"] = proximity(anchor, cand)[2]
            row.update({f"f_{k}": v for k, v in pair_features(a, b).items()})
        rows.append(row)
    return rows


# -- metrics ---------------------------------------------------------------------------------------------------
def _weights(y: np.ndarray, w: np.ndarray | None) -> np.ndarray:
    return np.ones(len(y)) if w is None else np.asarray(w, dtype=float)


def pr_curve(
    y: np.ndarray, s: np.ndarray, w: np.ndarray | None = None
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(thresholds high to low, precision, recall), one point per distinct score (ties stay together).
    `w` weighs each pair (see `pair_weights`); none means every pair counts once."""
    y, s = np.asarray(y), np.asarray(s, dtype=float)
    w = _weights(y, w)
    thr = np.unique(s)[::-1]
    pred = s[None, :] >= thr[:, None]
    tp = (pred * (w * (y == 1))[None, :]).sum(axis=1)
    return thr, tp / (pred * w[None, :]).sum(axis=1), tp / (float((w * (y == 1)).sum()) or 1.0)


def average_precision(y: np.ndarray, s: np.ndarray, w: np.ndarray | None = None) -> float:
    _, precision, recall = pr_curve(y, s, w)
    return float(np.sum(np.diff(np.concatenate([[0.0], recall])) * precision))


def prf(y: np.ndarray, pred: np.ndarray, w: np.ndarray | None = None) -> tuple[float, float, float]:
    y, pred = np.asarray(y).astype(bool), np.asarray(pred).astype(bool)
    w = _weights(y, w)
    tp, fp, fn = float(w[pred & y].sum()), float(w[pred & ~y].sum()), float(w[~pred & y].sum())
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return p, r, (2 * p * r / (p + r) if p + r else 0.0)


def best_f1(y: np.ndarray, s: np.ndarray, w: np.ndarray | None = None) -> tuple[float, float]:
    """(threshold, F1) at the best threshold. Chosen on the data it is reported on, so optimistic."""
    thr, precision, recall = pr_curve(y, s, w)
    with np.errstate(invalid="ignore", divide="ignore"):
        f1 = np.where(precision + recall > 0, 2 * precision * recall / (precision + recall), 0.0)
    i = int(np.argmax(f1))
    return float(thr[i]), float(f1[i])


# -- a small logistic regression (numpy only) -----------------------------------------------------------------------
@dataclass
class LogReg:
    mean: np.ndarray
    scale: np.ndarray
    weights: np.ndarray  # intercept first

    def predict(self, X: np.ndarray) -> np.ndarray:
        z = np.c_[np.ones(len(X)), (X - self.mean) / self.scale] @ self.weights
        return 1 / (1 + np.exp(-np.clip(z, -30, 30)))


def fit_logreg(X: np.ndarray, y: np.ndarray, l2: float = 1.0, iters: int = 100) -> LogReg:
    """L2-regularised logistic regression by Newton's method, on standardised features."""
    mean, scale = X.mean(axis=0), X.std(axis=0)
    scale[scale == 0] = 1.0
    Z = np.c_[np.ones(len(X)), (X - mean) / scale]
    w = np.zeros(Z.shape[1])
    ridge = l2 * np.eye(Z.shape[1])
    ridge[0, 0] = 0.0  # never shrink the intercept
    for _ in range(iters):
        p = 1 / (1 + np.exp(-np.clip(Z @ w, -30, 30)))
        gradient = Z.T @ (p - y) + ridge @ w
        hessian = Z.T @ (Z * (p * (1 - p))[:, None]) + ridge
        step = np.linalg.solve(hessian, gradient)
        w -= step
        if np.abs(step).max() < 1e-8:
            break
    return LogReg(mean, scale, w)


def cross_val_logreg(X: np.ndarray, y: np.ndarray, groups: np.ndarray, l2: float = 1.0) -> np.ndarray:
    """Out-of-sample scores: each group (window) is scored by a model trained on all the others."""
    out = np.zeros(len(y))
    for g in np.unique(groups):
        test = groups == g
        if len(np.unique(y[~test])) < 2:
            out[test] = float(y[~test].mean()) if (~test).any() else 0.5
        else:
            out[test] = fit_logreg(X[~test], y[~test], l2).predict(X[test])
    return out


# -- stage 3: the decision model --------------------------------------------------------------------------------------
DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
# Gates for acting on the model's P(same disaster). Defaults, not probed: set them from the numbers
# this benchmark prints. Yes at or above SAME_YES: link. No at or below SAME_NO: drop. In between:
# a person decides. A wrong "link" merges two disasters' impact figures; a wrong "drop" hides one.
SAME_YES = 0.7
SAME_NO = 0.3

SOURCE_LABELS = {
    "emdat": "EM-DAT disaster database record",
    "glide": "GLIDE disaster number record",
    "ifrcevent": "IFRC emergency page",
    "idmc": "IDMC displacement record",
    "cems": "Copernicus EMS activation",
    "charter": "International Charter activation",
    "usgs": "USGS earthquake catalogue entry",
    "gdacs": "GDACS alert",
    "pdc": "PDC alert",
    "ibtracs": "IBTrACS storm track",
    "desinventar": "DesInventar record",
    "gfd": "Global Flood Database event",
}
HAZARD_LABELS = {
    "EQ": "earthquake", "FL": "flood", "FF": "flash flood", "TC": "tropical cyclone", "ST": "storm",
    "VW": "wind storm", "WF": "wildfire", "LS": "landslide", "TO": "tornado", "DR": "drought",
    "VO": "volcanic activity", "TS": "tsunami", "EC": "extra-tropical cyclone", "HT": "heat wave",
    "CW": "cold wave", "SS": "storm surge", "AV": "avalanche", "EP": "epidemic", "OT": "other",
}  # fmt: skip

QUESTIONS = {
    "same_event": {
        "type": "noul",
        "instructions": (
            "Do record_a and record_b describe the same disaster: one earthquake sequence, one storm, "
            "one flood episode or one fire? A record can be a register entry for the whole disaster or a "
            "single alert, measurement or map activation for it. `days_apart` and `km_apart` were measured "
            "by the application between the two records; take them as given. Aftershocks minutes or hours "
            "after a main shock belong to the same disaster; a separate strong earthquake days or weeks "
            "later does not."
        ),
        "criteria": {
            "true": (
                "Both records are about one physical disaster, even when they differ in date precision, "
                "location detail, hazard wording or how many countries they name."
            ),
            "false": (
                "They are different disasters: a separate earthquake, a later flood or storm in the same "
                "country, a different hazard on the same day, or the same hazard in a distant region."
            ),
        },
    },
    "relation": {
        "type": "choice",
        "instructions": "How are the disasters described by record_a and record_b related?",
        "criteria": {
            "same_event": "They are one and the same disaster.",
            "one_caused_other": (
                "They are different hazards where one caused the other: a cyclone and the floods it brought, "
                "an earthquake and the landslides or tsunami it triggered."
            ),
            "unrelated": "They are different disasters with no causal link.",
        },
    },
}


# What a person labelling the records also looks at: the magnitude and the end date. False reproduces the
# first runs, which sent neither.
DESCRIBE_DETAIL = True


def describe(rec: Record) -> dict:
    """What the model reads about one record: words and facts, no ids and no codes."""
    out = {
        "kind": SOURCE_LABELS.get(rec.family, rec.family),
        "title": rec.title,
        "description": rec.text[:400],
        "hazard": HAZARD_LABELS.get(rec.glide, "unknown"),
        "starts": rec.start.date().isoformat() if rec.start else "unknown",
    }
    if DESCRIBE_DETAIL:
        out["ends"] = rec.end.date().isoformat() if rec.end else out["starts"]
        if rec.magnitude is not None:
            out["magnitude"] = rec.magnitude
    out["countries"] = [c for c in rec.countries if c != UNKNOWN_COUNTRY] or ["unknown"]
    return out


def decision_request(model: str, a: Record, b: Record, swap: bool = False) -> dict:
    """The request for one pair. Records go in a fixed order so position carries no meaning; `swap` reverses
    it, to check that the answer does not depend on the order."""
    _, _, gap, km = proximity(*pair_anchor(a, b))
    first, second = sorted((a, b), key=lambda r: r.id, reverse=swap)
    state = {
        "record_a": describe(first),
        "record_b": describe(second),
        "days_apart": int(round(gap)),
        "km_apart": "unknown" if km is None else int(round(km)),
    }
    return {"model": model, "state": state, "questions": QUESTIONS}


def parse_decision(response: dict) -> dict:
    """{p_same, p_cascade, relation, model, cost}; p_same is None when the model refused."""
    answers = response.get("answers") or {}
    same, relation = answers.get("same_event") or {}, answers.get("relation") or {}
    p_same = same.get("noul") if same.get("type") == "noul" else None
    probabilities = relation.get("probabilities") or {}
    return {
        "p_same": None if p_same is None else float(p_same),
        "p_cascade": probabilities.get("one_caused_other"),
        "relation": relation.get("choice"),
        "model": response.get("model"),
        "cost": (response.get("usage") or {}).get("cost") or 0.0,
    }


def gate(p_same: float | None) -> str:
    if p_same is None:
        return "review"
    return "same" if p_same >= SAME_YES else "different" if p_same <= SAME_NO else "review"


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


def score_with_model(
    pairs: list[tuple[Record, Record]], model: str, api_key: str, workers: int = 8, swap: bool = False
) -> list[dict]:
    """One result per pair, in order. A failed call gives {"error": ...} instead of raising."""

    def one(pair: tuple[Record, Record]) -> dict:
        try:
            return parse_decision(call_decisions(decision_request(model, *pair, swap=swap), api_key))
        except Exception as exc:  # one bad pair must not sink the run
            return {"p_same": None, "error": f"{type(exc).__name__}: {str(exc)[:160]}"}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(one, pairs))


# -- pipeline stages around a scorer: a date gate and candidate ranking -----------------------------------
def gate_scores(scores: np.ndarray, gap_days: np.ndarray, gate_days: float) -> np.ndarray:
    """Pairs whose records are more than `gate_days` apart are settled as different (score 0) before any scorer."""
    return np.where(np.asarray(gap_days) > gate_days, 0.0, scores)


def margin_scores(
    scores: np.ndarray,
    a: np.ndarray,
    b: np.ndarray,
    source_a: np.ndarray,
    source_b: np.ndarray,
    margin: float,
) -> np.ndarray:
    """Rank candidates against each other: a pair keeps its score only when it is within `margin` of the best
    score either of its records has in the other's source. Several matches per source can survive, so this is
    not one-to-one matching (a source can hold many records of one disaster)."""
    scores = np.asarray(scores, dtype=float)
    best: dict[tuple[str, str], float] = {}
    for x, y, fx, fy, sc in zip(a, b, source_a, source_b, scores, strict=True):
        if np.isnan(sc):
            continue
        for rec, fam in ((x, fy), (y, fx)):
            best[(rec, fam)] = max(best.get((rec, fam), -1.0), sc)
    keep = [
        np.isnan(sc) or (sc >= best[(x, fy)] - margin and sc >= best[(y, fx)] - margin)
        for x, y, fx, fy, sc in zip(a, b, source_a, source_b, scores, strict=True)
    ]
    return np.where(keep, scores, 0.0)


# -- stage 3, listwise: one request per record and source, all of that source's candidates together -------
EVENT_COLLECTIONS = [f"{prefix}-events" for prefix in _PREFIXES] + ["ibtracs-events"]

LISTWISE_NOTE = (
    "The candidates are records from one source. None, one or several may describe the same disaster as "
    "the anchor: a source can hold several records of one disaster, such as an alert for each shock. Judge "
    "every candidate against the anchor and against the other candidates; a candidate that fits the anchor "
    "worse than a rival, or differs in place or date, is probably a different disaster. `days_apart` and "
    "`km_apart` were measured by the application between the anchor and that candidate; take them as given. "
    "Aftershocks minutes or hours after a main shock belong to the same disaster; a separate strong "
    "earthquake days or weeks later does not."
)


def listwise_request(model: str, anchor: Record, candidates: list[Record]) -> dict:
    """One request: the anchor, its candidates keyed by name, a yes/no per candidate and the single best one."""
    keys = [f"candidate_{i + 1}" for i in range(len(candidates))]
    state_candidates = {}
    for key, cand in zip(keys, candidates, strict=True):
        _, _, gap, km = proximity(*pair_anchor(anchor, cand))
        state_candidates[key] = {
            **describe(cand),
            "days_apart": int(round(gap)),
            "km_apart": "unknown" if km is None else int(round(km)),
        }
    questions: dict[str, dict] = {
        f"same_{key}": {
            "type": "noul",
            "instructions": f"Does `candidates.{key}` describe the same disaster as `anchor`? {LISTWISE_NOTE}",
            "criteria": {
                "true": f"`candidates.{key}` is about the same physical disaster as `anchor`, whatever its date precision, location detail or wording.",
                "false": f"`candidates.{key}` is a different disaster: a separate earthquake, a later flood or storm, a different hazard, or a distant region.",
            },
        }
        for key in keys
    }
    questions["best"] = {
        "type": "choice",
        "instructions": f"Which candidate is the best match for `anchor`, the one most likely to be the same disaster? {LISTWISE_NOTE}",
        "criteria": {
            key: f"`candidates.{key}` ({cand.title[:60]}, {cand.start.date().isoformat() if cand.start else 'no date'}) is the best match."
            for key, cand in zip(keys, candidates, strict=True)
        },
    }
    questions["any_match"] = {
        "type": "noul",
        "instructions": f"Does at least one candidate describe the same disaster as `anchor`? {LISTWISE_NOTE}",
        "criteria": {
            "true": "At least one candidate is the same disaster as the anchor.",
            "false": "No candidate is the same disaster as the anchor.",
        },
    }
    return {
        "model": model,
        "state": {"anchor": describe(anchor), "candidates": state_candidates},
        "questions": questions,
    }


def parse_listwise(response: dict, n: int) -> dict:
    """{p_same: [n floats or None], best: [n floats or None], any_match, model, cost}, in candidate order."""
    answers = response.get("answers") or {}
    p_same = []
    for i in range(n):
        a = answers.get(f"same_candidate_{i + 1}") or {}
        p_same.append(float(a["noul"]) if a.get("type") == "noul" and a.get("noul") is not None else None)
    probabilities = (answers.get("best") or {}).get("probabilities") or {}
    any_match = answers.get("any_match") or {}
    return {
        "p_same": p_same,
        "best": [probabilities.get(f"candidate_{i + 1}") for i in range(n)],
        "any_match": any_match.get("noul") if any_match.get("type") == "noul" else None,
        "model": response.get("model"),
        "cost": (response.get("usage") or {}).get("cost") or 0.0,
    }


def listwise_groups(
    pairs: list[tuple[str, str]],
    records: dict[str, Record],
    max_candidates: int = 12,
    extra: dict[tuple[str, str], list[str]] | None = None,
) -> list[tuple[str, list[str]]]:
    """[(anchor id, candidate ids)]: one group per record and per other source family, split at `max_candidates`.

    `extra` adds unscored distractor ids to a (anchor id, family) group. Candidates are sorted by id so their
    order says nothing about the label.
    """
    by: dict[tuple[str, str], set[str]] = {}
    for a, b in pairs:
        by.setdefault((a, records[b].family), set()).add(b)
        by.setdefault((b, records[a].family), set()).add(a)
    for key, ids in (extra or {}).items():
        if key in by:
            by[key].update(ids)
    groups = []
    for (anchor, _family), ids in sorted(by.items()):
        ordered = sorted(ids)
        for i in range(0, len(ordered), max_candidates):
            groups.append((anchor, ordered[i : i + max_candidates]))
    return groups


def score_listwise(
    groups: list[tuple[str, list[str]]],
    records: dict[str, Record],
    model: str,
    api_key: str,
    workers: int = 8,
) -> list[dict]:
    """One result per group, in order. A failed call gives {"error": ...} instead of raising."""

    def one(group: tuple[str, list[str]]) -> dict:
        anchor, ids = group
        try:
            request = listwise_request(model, records[anchor], [records[i] for i in ids])
            return parse_listwise(call_decisions(request, api_key), len(ids))
        except Exception as exc:  # one bad group must not sink the run
            return {
                "p_same": [None] * len(ids),
                "best": [None] * len(ids),
                "error": f"{type(exc).__name__}: {str(exc)[:160]}",
            }

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(one, groups))


def pair_scores_from_listwise(
    groups: list[tuple[str, list[str]]], results: list[dict], pairs: list[tuple[str, str]]
) -> tuple[np.ndarray, np.ndarray]:
    """(score, best) per pair: the mean of the two directions (each record as anchor), NaN when no call answered."""
    directional: dict[tuple[str, str], tuple[float | None, float | None]] = {}
    for (anchor, ids), res in zip(groups, results, strict=True):
        for cand, p, best in zip(ids, res["p_same"], res["best"], strict=True):
            directional[(anchor, cand)] = (p, best)

    def mean(values):
        values = [v for v in values if v is not None]
        return float(np.mean(values)) if values else np.nan

    score = np.array(
        [
            mean([directional.get((a, b), (None, None))[0], directional.get((b, a), (None, None))[0]])
            for a, b in pairs
        ]
    )
    best = np.array(
        [
            mean([directional.get((a, b), (None, None))[1], directional.get((b, a), (None, None))[1]])
            for a, b in pairs
        ]
    )
    return score, best


def fetch_context(
    api_url: str,
    token: str,
    countries: set[str] | None,
    start: datetime,
    end: datetime,
    max_items: int = 1500,
    timeout: int = 90,
) -> list[dict]:
    """Event items of every source in these countries (all countries when None) and dates: the realistic
    crowd around a disaster."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    body = {
        "collections": EVENT_COLLECTIONS,
        "limit": 200 if countries else 1000,
        "datetime": f"{start.strftime('%Y-%m-%dT%H:%M:%SZ')}/{end.strftime('%Y-%m-%dT%H:%M:%SZ')}",
    }
    if countries:
        body["filter-lang"] = "cql2-json"
        body["filter"] = {
            "op": "a_overlaps",
            "args": [{"property": "monty:country_codes"}, sorted(countries)],
        }
    items: list[dict] = []
    url, payload = f"{api_url.rstrip('/')}/search", body
    while url and len(items) < max_items:
        r = requests.post(url, json=payload, headers=headers, timeout=timeout)
        r.raise_for_status()
        page = r.json()
        items += page.get("features", [])
        nxt = next((link for link in page.get("links", []) if link.get("rel") == "next"), None)
        url, payload = (nxt["href"], nxt.get("body") or body) if nxt else (None, None)
    return items[:max_items]


def choose_distractors(
    keys: list[tuple[str, str]],
    records: dict[str, Record],
    context: dict[str, Record],
    gate_days: float,
    per_group: int,
) -> dict[tuple[str, str], list[str]]:
    """{(anchor id, family): context ids}: the closest unlabelled records of that source that could be rivals."""
    by_family: dict[str, list[Record]] = {}
    for rec in context.values():
        by_family.setdefault(rec.family, []).append(rec)
    out: dict[tuple[str, str], list[str]] = {}
    for anchor_id, family in keys:
        anchor = records[anchor_id]
        rivals = [c for c in by_family.get(family, []) if block(anchor, c, gate_days) == "ok"]
        rivals.sort(key=lambda c: (gap_days(anchor, c, by_day=True), c.id))
        out[(anchor_id, family)] = [c.id for c in rivals[:per_group]]
    return out


# -- scope: destructive events ------------------------------------------------------------------------------
# Sources that record a disaster only when it hurt people or property: registers, response operations,
# displacement figures and damage maps. A pair is in scope when at least one record comes from them; a
# small offshore quake that only USGS and GDACS report needs no pairing.
IMPACT_SOURCES = frozenset({"emdat", "glide", "ifrcevent", "idmc", "desinventar", "cems", "charter"})


def destructive(rec: Record) -> bool:
    return rec.family in IMPACT_SOURCES


# -- duplicates, uncertainty and confidence ------------------------------------------------------------------
def copy_counts(records: dict[str, Record], label: dict[str, str]) -> dict[str, int]:
    """How many identical copies each record has in its disaster: same source family, title and dates.

    IDMC publishes one record twice (IDU and GIDD) and DesInventar repeats rows, so without weights their
    pairs count two to four times.
    """

    def key(r: Record) -> tuple:
        return (label.get(r.id), r.family, r.title, r.start and r.start.date(), r.end and r.end.date())

    counts: dict[tuple, int] = {}
    for r in records.values():
        counts[key(r)] = counts.get(key(r), 0) + 1
    return {i: counts[key(r)] for i, r in records.items()}


def pair_weights(a, b, copies: dict[str, int]) -> np.ndarray:
    """1 / (copies of a x copies of b): a pair repeated through identical copies counts once in all."""
    return np.array([1.0 / (copies[x] * copies[y]) for x, y in zip(a, b, strict=True)])


def bootstrap_f1(
    y: np.ndarray,
    scores: dict[str, tuple[np.ndarray, float]],
    groups: np.ndarray,
    w: np.ndarray | None = None,
    n: int = 1000,
    seed: int = 0,
) -> dict[str, np.ndarray]:
    """{scorer: n F1 values} over resamples of whole windows, at each scorer's threshold.

    Pairs inside a window share records, so windows, not pairs, are resampled. Every scorer sees the same
    resamples, so the difference of two scorers' samples gives a paired interval for the difference.
    """
    y, groups, w = np.asarray(y), np.asarray(groups), _weights(y, w)
    rng = np.random.default_rng(seed)
    windows = np.unique(groups)
    rows = {g: np.flatnonzero(groups == g) for g in windows}
    out = {name: np.empty(n) for name in scores}
    for i in range(n):
        take = np.concatenate([rows[g] for g in rng.choice(windows, len(windows))])
        for name, (s, thr) in scores.items():
            s = np.asarray(s, dtype=float)
            t = take[~np.isnan(s[take])]
            out[name][i] = prf(y[t], s[t] >= thr, w[t])[2]
    return out


def interval(samples: np.ndarray, level: float = 0.95) -> tuple[float, float]:
    lo, hi = np.quantile(samples, [(1 - level) / 2, (1 + level) / 2])
    return float(lo), float(hi)


def calibration_table(y: np.ndarray, p: np.ndarray, bins: int = 5, w: np.ndarray | None = None) -> list[dict]:
    """Per probability bin: pairs, mean predicted P(same) and the share that really is the same disaster.
    A calibrated scorer has the two close, which is what a stored link confidence needs."""
    y, p, w = np.asarray(y, dtype=float), np.asarray(p, dtype=float), _weights(y, w)
    ok = ~np.isnan(p)
    edges = np.linspace(0, 1, bins + 1)
    which = np.clip(np.digitize(p, edges[1:-1]), 0, bins - 1)
    rows = []
    for k in range(bins):
        m = ok & (which == k)
        if m.any():
            rows.append({
                "P(same) bin": f"{edges[k]:.1f}-{edges[k + 1]:.1f}",
                "pairs": int(m.sum()),
                "mean P(same)": float(np.average(p[m], weights=w[m])),
                "share same": float(np.average(y[m], weights=w[m])),
            })  # fmt: skip
    return rows


def brier(y: np.ndarray, p: np.ndarray, w: np.ndarray | None = None) -> float:
    """Mean squared error of P(same) against the label (0 is perfect, 0.25 is a coin flip)."""
    y, p, w = np.asarray(y, dtype=float), np.asarray(p, dtype=float), _weights(y, w)
    ok = ~np.isnan(p)
    return float(np.average((p[ok] - y[ok]) ** 2, weights=w[ok]))


# -- from pairs to events -----------------------------------------------------------------------------------
def assemble_events(ids, a, b, scores, thr: float) -> dict[str, int]:
    """{record id: event number}: the connected groups of records joined by pairs scored at or above `thr`."""
    parent = {i: i for i in ids}

    def root(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for x, y, s in zip(a, b, scores, strict=True):
        if s is not None and not np.isnan(s) and s >= thr:
            parent[root(x)] = root(y)
    numbers: dict[str, int] = {}
    return {i: numbers.setdefault(root(i), len(numbers)) for i in sorted(parent)}


def bcubed(event_of: dict[str, int], label: dict[str, str]) -> tuple[float, float, float]:
    """B-cubed (precision, recall, F1) of assembled events against the gold disasters, averaged per record.

    Precision: of the records grouped with a record, the share from its disaster. Recall: of its disaster's
    records, the share grouped with it.
    """
    ids = [i for i in event_of if i in label]
    if not ids:
        return 0.0, 0.0, 0.0
    by_event: dict[int, set[str]] = {}
    by_label: dict[str, set[str]] = {}
    for i in ids:
        by_event.setdefault(event_of[i], set()).add(i)
        by_label.setdefault(label[i], set()).add(i)
    p = float(
        np.mean([len(by_event[event_of[i]] & by_label[label[i]]) / len(by_event[event_of[i]]) for i in ids])
    )
    r = float(
        np.mean([len(by_event[event_of[i]] & by_label[label[i]]) / len(by_label[label[i]]) for i in ids])
    )
    return p, r, (2 * p * r / (p + r) if p + r else 0.0)


# -- links a source already makes ----------------------------------------------------------------------------
# One id a source gives to every record of one disaster: EM-DAT's DisNo and the GLIDE number repeat on each
# affected country's record, IDMC publishes one event as IDU and GIDD, GDACS keeps one event id across
# episodes. These link records of one source across countries for free; scorers never see them.
_SOURCE_KEYS = (
    ("emdat", re.compile(r"^emdat-event-(\d{4}-\d{4})-[A-Z]{3}$")),
    ("glide", re.compile(r"^glide-event-([A-Z]{2}-\d{4}-\d{6})-[A-Z]{3}$")),
    ("idmc", re.compile(r"^idmc-(?:idu|gidd)-event-(\d+)$")),
    ("gdacs", re.compile(r"^gdacs-event-(\d+)-\d+$")),
)


def source_key(item_id: str) -> str | None:
    """ "emdat:2023-0095" for every country record of EM-DAT disaster 2023-0095, and so on; None otherwise."""
    for name, pattern in _SOURCE_KEYS:
        if m := pattern.match(item_id):
            return f"{name}:{m.group(1)}"
    return None


def same_source_links(ids) -> list[tuple[str, str]]:
    """Pairs of records that share a source key, chained (a-b, b-c) so each group is connected."""
    groups: dict[str, list[str]] = {}
    for i in sorted(ids):
        if key := source_key(i):
            groups.setdefault(key, []).append(i)
    return [(g[k], g[k + 1]) for g in groups.values() for k in range(len(g) - 1)]
