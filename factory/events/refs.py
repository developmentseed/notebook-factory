"""
Event references: normalised `scheme:value` strings naming a disaster in some source.

A disaster is known by many IDs (a GLIDE number, an IFRC GO emergency, an EMSR
activation, a USGS event id, a monty corr_id, a STAC item…). Items and runs store
the refs they carry in `event_refs`; the disaster page groups everything whose
refs overlap (see factory.events.disasters).

Schemes are Montandon source names (the collection prefix: `glide`, `gdacs`,
`ifrcevent`, `idmc-idu`…) plus:
- `corr` / `corrstem`: monty:corr_id, and the same without its episode suffix
- `stac`: `<collection>/<item id>` for any catalog

Pure functions only (no models): the 0008 data migration imports them.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

ROLE_SUFFIXES = ("-events", "-hazards", "-impacts", "-response")
# Typed prefixes that are friendlier than the source name.
SCHEME_ALIASES = {"go": "ifrcevent", "ifrc": "ifrcevent", "emsr": "cems", "monty": "corr", "corr_id": "corr"}
KNOWN_SCHEMES = {
    "cems",
    "corr",
    "corrstem",
    "desinventar",
    "emdat",
    "gdacs",
    "gfd",
    "glide",
    "ibtracs",
    "idmc-gidd",
    "idmc-idu",
    "ifrcevent",
    "pdc",
    "stac",
    "usgs",
}

GLIDE_RE = re.compile(r"^([A-Z]{2})-(\d{4})-(\d{6})(?:-([A-Z]{3}))?$")
EMSR_RE = re.compile(r"^EMSR\d+$")
EMDAT_RE = re.compile(r"^(\d{4}-\d{4})(?:-([A-Z]{3}))?$")
CORR_RE = re.compile(r"^\d{8}-[A-Z]{3}-.+-GCDB$")
GDACS_RE = re.compile(r"^(?:EQ|TC|FL|VO|DR|WF|TS)\s?(\d{6,8})$")
USGS_RE = re.compile(r"^[a-z]{2}(?=[0-9a-z]*\d)[0-9a-z]{6,12}$")
ITEM_ID_RE = re.compile(r"^([a-z]+(?:-[a-z]+)?)-(event|hazard|impact)-(.+)$")


def ref(scheme: str, value: str) -> str:
    return f"{scheme}:{value}"


def split(r: str) -> tuple[str, str]:
    scheme, _, value = r.partition(":")
    return scheme, value


def source_of(collection: str) -> str:
    """`idmc-idu-events` → `idmc-idu`."""
    for suffix in ROLE_SUFFIXES:
        if collection.endswith(suffix):
            return collection[: -len(suffix)]
    return collection


def corr_stem(corr_id: str) -> str:
    """The corr_id without its `-<episode>-GCDB` tail (GDACS puts its episode id there)."""
    if corr_id.endswith("-GCDB") and corr_id.count("-") >= 3:
        return corr_id.rsplit("-", 2)[0]
    return corr_id


def glide_refs(glide: str) -> list[str]:
    """`EQ-2025-000043-MMR` is also findable as `EQ-2025-000043` (the country suffix is often left off)."""
    m = GLIDE_RE.match(glide)
    if not m:
        return []
    core = "-".join(m.groups()[:3])
    return [ref("glide", glide), ref("glide", core)] if m.group(4) else [ref("glide", core)]


def corr_refs(corr_id: str) -> list[str]:
    return [ref("corr", corr_id), ref("corrstem", corr_stem(corr_id))] if corr_id else []


def _source_value(source: str, item_id: str, src_event_id: str = "") -> list[str]:
    """Refs for a source's own event id."""
    if source == "glide":
        m = ITEM_ID_RE.match(item_id)
        return glide_refs(m.group(3)) if m else []
    if source == "gdacs":
        # gdacs-event-<event>-<episode>; CEMS also links to bare `<event>-<episode>` ids
        m = ITEM_ID_RE.match(item_id)
        value = src_event_id or (m.group(3) if m else item_id).split("-")[0]
        return [ref("gdacs", value)] if value.isdigit() else []
    if source == "emdat":
        m = EMDAT_RE.match(src_event_id or "")
        if not m:
            return []
        return (
            [ref("emdat", src_event_id), ref("emdat", m.group(1))]
            if m.group(2)
            else [ref("emdat", src_event_id)]
        )
    if source == "cems":
        m = ITEM_ID_RE.match(item_id)
        value = m.group(3).split("-")[0] if m else ""
        return [ref("cems", value)] if EMSR_RE.match(value) else []
    if src_event_id:
        return [ref(source, src_event_id)]
    m = ITEM_ID_RE.match(item_id)  # e.g. usgs-event-us7000pn9s, ifrcevent-event-7464
    return [ref(source, m.group(3))] if m and "-" not in m.group(3) else []


def item_refs(item: dict, *, monty: bool) -> list[str]:
    """All refs of a raw STAC item. Generic catalogs only get `stac:`."""
    collection = item.get("collection") or ""
    item_id = item.get("id") or ""
    out = [ref("stac", f"{collection}/{item_id}")] if collection and item_id else []
    if monty:
        props = item.get("properties") or {}
        source = source_of(collection)
        out += _source_value(source, item_id, str(props.get("monty:src_event_id") or ""))
        out += corr_refs(props.get("monty:corr_id") or "")
        out += related_event_refs(item)
    return list(dict.fromkeys(out))


def link_target(href: str) -> tuple[str, str] | None:
    """(collection, item_id) of a STAC item URL."""
    m = re.search(r"/collections/([^/]+)/items/([^/?#]+)", href)
    return (m.group(1), m.group(2)) if m else None


def related_event_refs(item: dict) -> list[str]:
    """Refs of other sources' events this item links to (CEMS → GDACS / Charter): the same disaster."""
    source = source_of(item.get("collection") or "")
    out = []
    for link in item.get("links") or []:
        if link.get("rel") != "related" or "hazard" in (link.get("roles") or []):
            continue
        target = link_target(link.get("href") or "")
        if target and target[0].endswith("-events") and source_of(target[0]) != source:
            out += _source_value(source_of(target[0]), target[1])
    return out


def primary_ref(item: dict, *, monty: bool) -> str:
    """The one ref naming this very item: its source id when it has one, else `stac:`."""
    refs = item_refs(item, monty=monty)
    for r in refs:
        if split(r)[0] not in {"stac", "corr", "corrstem"}:
            return r
    return refs[0] if refs else ""


def parse(text: str) -> list[str]:
    """Refs a user typed: an ID in any known form, a URL, or an explicit `scheme:value`."""
    q = (text or "").strip()
    if not q:
        return []
    if q.startswith(("http://", "https://")):
        return _parse_url(q)
    scheme, sep, value = q.partition(":")
    scheme = SCHEME_ALIASES.get(scheme.lower(), scheme.lower())
    if sep and scheme in KNOWN_SCHEMES and value.strip():
        value = value.strip()
        if scheme == "glide":
            return glide_refs(value.upper()) or [ref("glide", value.upper())]
        if scheme == "corr":
            return corr_refs(value)
        if scheme == "cems":
            value = value.upper() if value.upper().startswith("EMSR") else f"EMSR{value}"
        return [ref(scheme, value)]
    up = q.upper()
    if GLIDE_RE.match(up):
        return glide_refs(up)
    if EMSR_RE.match(up):
        return [ref("cems", up)]
    if CORR_RE.match(q):
        return corr_refs(q)
    if EMDAT_RE.match(up):
        return [ref("emdat", up)]
    if m := GDACS_RE.match(up):
        return [ref("gdacs", m.group(1))]
    if m := ITEM_ID_RE.match(q):
        # a Montandon item id, e.g. glide-event-EQ-2025-000043-MMR
        source = m.group(1)
        out = [ref("stac", f"{source}-{m.group(2)}s/{q}")]
        if m.group(2) == "event":
            out += _source_value(source, q)
        return out
    if "/" in q and " " not in q:
        return [ref("stac", q)]
    if USGS_RE.match(q):
        return [ref("usgs", q)]
    return []


def _parse_url(url: str) -> list[str]:
    u = urlparse(url)
    host = u.netloc.lower()
    if target := link_target(u.path):
        return parse(f"{target[0]}/{target[1]}") if ITEM_ID_RE.match(target[1]) is None else parse(target[1])
    if "go.ifrc.org" in host and (m := re.search(r"/emergencies/(\d+)", u.path)):
        return [ref("ifrcevent", m.group(1))]
    if "emergency.copernicus.eu" in host and (m := re.search(r"(EMSR\d+)", u.path, re.I)):
        return [ref("cems", m.group(1).upper())]
    if "earthquake.usgs.gov" in host and (m := re.search(r"/eventpage/([0-9a-z]+)", u.path)):
        return [ref("usgs", m.group(1))]
    if "gdacs.org" in host and (eventid := parse_qs(u.query).get("eventid")):
        return [ref("gdacs", eventid[0])]
    if "emdat.be" in host and (m := re.search(r"(\d{4}-\d{4}(?:-[A-Z]{3})?)", u.path)):
        return [ref("emdat", m.group(1))]
    if "glidenumber.net" in host and (glide := parse_qs(u.query).get("glide")):
        return glide_refs(glide[0].upper())
    return []


def label(r: str) -> str:
    """Human label for a ref chip."""
    scheme, value = split(r)
    names = {
        "glide": "GLIDE",
        "ifrcevent": "IFRC GO",
        "cems": "Copernicus EMS",
        "emdat": "EM-DAT",
        "gdacs": "GDACS",
        "usgs": "USGS",
        "pdc": "PDC",
        "idmc-idu": "IDMC IDU",
        "idmc-gidd": "IDMC GIDD",
        "desinventar": "DesInventar",
        "gfd": "GFD",
        "ibtracs": "IBTrACS",
        "corr": "corr_id",
        "corrstem": "corr_id stem",
        "stac": "STAC",
        "charter": "Charter",
    }
    return f"{names.get(scheme, scheme)} {value}"


def external_url(r: str) -> str:
    """A public page for the ref, when there is a stable one."""
    scheme, value = split(r)
    if scheme == "ifrcevent":
        return f"https://go.ifrc.org/emergencies/{value}"
    if scheme == "cems":
        return f"https://rapidmapping.emergency.copernicus.eu/{value}"
    if scheme == "usgs":
        return f"https://earthquake.usgs.gov/earthquakes/eventpage/{value}"
    if scheme == "emdat" and (m := EMDAT_RE.match(value)) and m.group(2):
        return f"https://public.emdat.be/data/{value}"
    return ""
