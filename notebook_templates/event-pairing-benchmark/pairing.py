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
def block(a: Record, b: Record, block_days: float) -> str:
    """ "ok" when the pair is a candidate, else the reason it is not: "country" or "time".

    An unknown country (UNK or none) never blocks: upstream sources do mislabel countries.
    """
    ca, cb = set(a.countries) - {UNKNOWN_COUNTRY}, set(b.countries) - {UNKNOWN_COUNTRY}
    if ca and cb and not ca & cb:
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
            row.update({f"f_{k}": v for k, v in pair_features(a, b).items()})
        rows.append(row)
    return rows


# -- metrics ---------------------------------------------------------------------------------------------------
def pr_curve(y: np.ndarray, s: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(thresholds high to low, precision, recall), one point per distinct score (ties stay together)."""
    y, s = np.asarray(y), np.asarray(s, dtype=float)
    thr = np.unique(s)[::-1]
    pred = s[None, :] >= thr[:, None]
    tp = (pred & (y[None, :] == 1)).sum(axis=1)
    return thr, tp / pred.sum(axis=1), tp / max(int(y.sum()), 1)


def average_precision(y: np.ndarray, s: np.ndarray) -> float:
    _, precision, recall = pr_curve(y, s)
    return float(np.sum(np.diff(np.concatenate([[0.0], recall])) * precision))


def prf(y: np.ndarray, pred: np.ndarray) -> tuple[float, float, float]:
    y, pred = np.asarray(y).astype(bool), np.asarray(pred).astype(bool)
    tp, fp, fn = int((pred & y).sum()), int((pred & ~y).sum()), int((~pred & y).sum())
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return p, r, (2 * p * r / (p + r) if p + r else 0.0)


def best_f1(y: np.ndarray, s: np.ndarray) -> tuple[float, float]:
    """(threshold, F1) at the best threshold. Chosen on the data it is reported on, so optimistic."""
    thr, precision, recall = pr_curve(y, s)
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


def describe(rec: Record) -> dict:
    """What the model reads about one record: words and facts, no ids and no codes."""
    return {
        "kind": SOURCE_LABELS.get(rec.family, rec.family),
        "title": rec.title,
        "description": rec.text[:400],
        "hazard": HAZARD_LABELS.get(rec.glide, "unknown"),
        "starts": rec.start.date().isoformat() if rec.start else "unknown",
        "countries": [c for c in rec.countries if c != UNKNOWN_COUNTRY] or ["unknown"],
    }


def decision_request(model: str, a: Record, b: Record) -> dict:
    _, _, gap, km = proximity(*pair_anchor(a, b))
    first, second = sorted((a, b), key=lambda r: r.id)  # a fixed order, so position carries no meaning
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
    pairs: list[tuple[Record, Record]], model: str, api_key: str, workers: int = 8
) -> list[dict]:
    """One result per pair, in order. A failed call gives {"error": ...} instead of raising."""

    def one(pair: tuple[Record, Record]) -> dict:
        try:
            return parse_decision(call_decisions(decision_request(model, *pair), api_key))
        except Exception as exc:  # one bad pair must not sink the run
            return {"p_same": None, "error": f"{type(exc).__name__}: {str(exc)[:160]}"}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(one, pairs))
