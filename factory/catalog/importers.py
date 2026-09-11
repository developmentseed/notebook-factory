"""
Importers for administrative boundaries.

`import_features` is source-agnostic: it takes an iterable of GDAL features and
a field mapping. `GADM_MAPPING` covers GADM 4.1 GeoJSON/GeoPackage/Shapefile
exports; a COD mapping would map P-codes the same way.
"""

from __future__ import annotations

import io
import logging
import zipfile
from dataclasses import dataclass
from pathlib import Path

import httpx
from django.contrib.gis.gdal import DataSource
from django.contrib.gis.geos import GEOSGeometry, MultiPolygon, Polygon
from django.db import transaction

from .models import AdminArea, AdminAreaSource, Country

log = logging.getLogger(__name__)

GADM_URL = "https://geodata.ucdavis.edu/gadm/gadm4.1/json/gadm41_{iso3}_{level}.json.zip"


@dataclass
class FieldMapping:
    """How to read a boundary feature at a given level."""

    code: str  # attribute holding the unique code
    name: str  # attribute holding the display name
    parent_code: str | None = None  # attribute holding the parent's code
    country_iso3: str = "GID_0"
    country_name: str | None = "COUNTRY"


def gadm_mapping(level: int) -> FieldMapping:
    return FieldMapping(
        code=f"GID_{level}",
        name=f"NAME_{level}" if level > 0 else "COUNTRY",
        parent_code=f"GID_{level - 1}" if level > 0 else None,
    )


def cod_mapping(level: int) -> FieldMapping:
    return FieldMapping(
        code=f"ADM{level}_PCODE",
        name=f"ADM{level}_EN",
        parent_code=f"ADM{level - 1}_PCODE" if level > 0 else None,
        country_iso3="ADM0_PCODE",
        country_name="ADM0_EN",
    )


def _to_multipolygon(geom: GEOSGeometry) -> MultiPolygon | None:
    if geom is None or geom.empty:
        return None
    if geom.srid and geom.srid != 4326:
        geom.transform(4326)
    geom.srid = 4326
    if isinstance(geom, Polygon):
        return MultiPolygon(geom, srid=4326)
    if isinstance(geom, MultiPolygon):
        return geom
    if geom.geom_type == "GeometryCollection":
        polys = [g for g in geom if isinstance(g, Polygon)]
        return MultiPolygon(*polys, srid=4326) if polys else None
    return None


def import_datasource(
    path: str | Path,
    *,
    level: int,
    mapping: FieldMapping,
    source: str = AdminAreaSource.GADM,
    country_iso3: str | None = None,
    layer_index: int = 0,
) -> tuple[int, int]:
    """Import all features from a GDAL-readable file. Returns (created, updated)."""
    ds = DataSource(str(path))
    layer = ds[layer_index]
    fields = set(layer.fields)
    created = updated = 0
    with transaction.atomic():
        for feature in layer:
            props = {f: feature.get(f) for f in fields}
            iso3 = (country_iso3 or props.get(mapping.country_iso3) or "").upper()[:3]
            if not iso3:
                log.warning("Skipping feature without country code: %s", props)
                continue
            country, _ = Country.objects.get_or_create(
                iso3=iso3,
                defaults={"name": (mapping.country_name and props.get(mapping.country_name)) or iso3},
            )
            code = str(props.get(mapping.code) or "").strip()
            name = str(props.get(mapping.name) or code).strip()
            if not code:
                log.warning("Skipping feature without code: %s", props)
                continue
            parent = None
            if mapping.parent_code and props.get(mapping.parent_code):
                parent = AdminArea.objects.filter(source=source, code=props[mapping.parent_code]).first()
            geom = _to_multipolygon(feature.geom.geos)
            extra = {
                k: v
                for k, v in props.items()
                if v not in (None, "") and k not in {mapping.code, mapping.name}
            }
            _, was_created = AdminArea.objects.update_or_create(
                source=source,
                code=code,
                defaults={
                    "name": name,
                    "level": level,
                    "country": country,
                    "parent": parent,
                    "geom": geom,
                    "properties": extra,
                    "area_km2": None,
                },
            )
            created += was_created
            updated += not was_created
    return created, updated


def download_gadm(iso3: str, level: int, dest_dir: Path) -> Path:
    """Download a GADM 4.1 GeoJSON for one country/level and return the extracted path."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / f"gadm41_{iso3}_{level}.json"
    if target.exists():
        return target
    url = GADM_URL.format(iso3=iso3.upper(), level=level)
    log.info("Downloading %s", url)
    with httpx.Client(timeout=120, follow_redirects=True) as client:
        r = client.get(url)
        r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        names = [n for n in zf.namelist() if n.endswith(".json")]
        if not names:
            raise RuntimeError(f"No JSON in {url}")
        target.write_bytes(zf.read(names[0]))
    return target
