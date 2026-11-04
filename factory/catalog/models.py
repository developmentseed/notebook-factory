"""
Reference data used to parameterise and index notebook runs: filters (facets
such as hazard or use case, with their options), countries and administrative
areas. Geometries are stored so the app can offer a map picker and export
boundaries to notebooks; the analysis itself never runs here.
"""

from django.contrib.gis.db import models as gis_models
from django.core.exceptions import ValidationError
from django.db import models

# Query parameters the browse page and the API already use; a facet key must not shadow them.
RESERVED_FACET_KEYS = {
    "q",
    "country",
    "status",
    "period",
    "sort",
    "template",
    "page",
    "catalog",
    "notebook",
    "area",
    "trigger",
    "batch",
    "since",
    "until",
    "search",
    "ordering",
    "format",
    "stac_item",
}


def validate_facet_key(value: str) -> None:
    if value in RESERVED_FACET_KEYS:
        raise ValidationError(f"'{value}' is reserved for other filters; pick another key.")


class Facet(models.Model):
    """A filter dimension (e.g. Hazard, Use case, Sector) that templates, runs and STAC items are tagged with."""

    key = models.SlugField(
        max_length=40,
        unique=True,
        validators=[validate_facet_key],
        help_text="Used in URLs (?hazard=flood) and as the template parameter name it maps from",
    )
    label = models.CharField(max_length=80)
    description = models.CharField(max_length=300, blank=True)
    multiple = models.BooleanField(default=False, help_text="A run may carry several options of this filter")
    stac_property = models.CharField(
        max_length=120,
        blank=True,
        help_text="STAC item property whose values map onto options via their match codes, e.g. monty:hazard_codes",
    )
    show_in_browse = models.BooleanField(default=True, help_text="Offer this filter on the browse page")
    order = models.PositiveSmallIntegerField(default=100)

    class Meta:
        ordering = ["order", "label"]

    def __str__(self) -> str:
        return self.label


class FacetOption(models.Model):
    """One value of a facet (e.g. Hazard → Flood)."""

    facet = models.ForeignKey(Facet, on_delete=models.CASCADE, related_name="options")
    key = models.SlugField(max_length=60)
    label = models.CharField(max_length=80)
    icon = models.CharField(max_length=8, blank=True, help_text="Emoji shown in the UI")
    color = models.CharField(max_length=7, blank=True, help_text="Hex colour used for badges")
    match_codes = models.JSONField(
        default=list,
        blank=True,
        help_text="Code prefixes in the facet's STAC property that map to this option, e.g. ['nat-hyd-flo']",
    )
    order = models.PositiveSmallIntegerField(default=100)

    class Meta:
        ordering = ["facet__order", "order", "label"]
        constraints = [models.UniqueConstraint(fields=["facet", "key"], name="uniq_facetoption_facet_key")]

    def __str__(self) -> str:
        return f"{self.facet.label}: {self.label}"

    def matches(self, value) -> bool:
        """True when a STAC property value is this option's key or starts with one of its match codes."""
        value = str(value or "").lower()
        if not value:
            return False
        return value == self.key.lower() or any(value.startswith(p.lower()) for p in self.match_codes or [])


class Country(models.Model):
    iso3 = models.CharField(max_length=3, primary_key=True)
    iso2 = models.CharField(max_length=2, blank=True)
    name = models.CharField(max_length=120)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "countries"

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        self.iso3 = self.iso3.upper()
        self.iso2 = self.iso2.upper()
        super().save(*args, **kwargs)


class AdminAreaSource(models.TextChoices):
    GADM = "gadm", "GADM"
    COD = "cod", "COD (OCHA Common Operational Datasets)"
    CUSTOM = "custom", "Custom"


class AdminAreaQuerySet(models.QuerySet):
    def in_country(self, iso3: str):
        return self.filter(country_id=iso3.upper())

    def at_level(self, level: int):
        return self.filter(level=level)

    def light(self):
        """Defer the (potentially large) geometry column."""
        return self.defer("geom")


class AdminArea(models.Model):
    """An administrative unit (country = level 0, province = level 1, district = level 2, ...)."""

    code = models.CharField(
        max_length=64, help_text="Stable identifier from the source, e.g. GADM GID or P-code"
    )
    name = models.CharField(max_length=200)
    level = models.PositiveSmallIntegerField()
    country = models.ForeignKey(Country, on_delete=models.CASCADE, related_name="areas")
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.CASCADE, related_name="children"
    )
    source = models.CharField(max_length=16, choices=AdminAreaSource.choices, default=AdminAreaSource.GADM)
    geom = gis_models.MultiPolygonField(srid=4326, null=True, blank=True)
    bbox = models.JSONField(null=True, blank=True, help_text="[minx, miny, maxx, maxy] in WGS84")
    centroid = gis_models.PointField(srid=4326, null=True, blank=True)
    area_km2 = models.FloatField(null=True, blank=True)
    properties = models.JSONField(default=dict, blank=True, help_text="Extra attributes from the source")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = AdminAreaQuerySet.as_manager()

    class Meta:
        ordering = ["country", "level", "name"]
        constraints = [models.UniqueConstraint(fields=["source", "code"], name="uniq_area_source_code")]
        indexes = [
            models.Index(fields=["country", "level"]),
            models.Index(fields=["level"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.country_id} · L{self.level})"

    def save(self, *args, **kwargs):
        if self.geom is not None:
            self.bbox = list(self.geom.extent)
            self.centroid = self.geom.centroid
            if self.area_km2 is None:
                try:
                    self.area_km2 = round(self.geom.transform(6933, clone=True).area / 1e6, 2)
                except Exception:  # pragma: no cover - projection edge cases
                    self.area_km2 = None
        super().save(*args, **kwargs)

    @property
    def display_level(self) -> str:
        return {0: "Country", 1: "Admin 1", 2: "Admin 2", 3: "Admin 3"}.get(self.level, f"Admin {self.level}")

    @property
    def breadcrumb(self) -> str:
        parts = [self.name]
        parent = self.parent
        while parent is not None:
            parts.append(parent.name)
            parent = parent.parent
        return " › ".join(reversed(parts))

    # -- GeoJSON helpers -----------------------------------------------------
    def as_feature(self, simplify: float | None = None, geometry: bool = True) -> dict:
        geom = None
        if geometry and self.geom is not None:
            g = self.geom.simplify(simplify, preserve_topology=True) if simplify else self.geom
            geom = g.json
        import json

        return {
            "type": "Feature",
            "id": self.pk,
            "geometry": json.loads(geom) if geom else None,
            "bbox": self.bbox,
            "properties": {
                "id": self.pk,
                "code": self.code,
                "name": self.name,
                "level": self.level,
                "country": self.country_id,
                "parent": self.parent_id,
                "source": self.source,
                "area_km2": self.area_km2,
                **{k: v for k, v in (self.properties or {}).items() if k not in {"geometry"}},
            },
        }

    def descendants_at_level(self, level: int):
        """All areas nested under this one at the given (deeper) level."""
        qs = AdminArea.objects.filter(country=self.country, level=level)
        if level <= self.level:
            return qs.filter(pk=self.pk)
        # Walk the parent chain: parent, parent__parent, ...
        lookup = "__".join(["parent"] * (level - self.level))
        return qs.filter(**{lookup: self})
