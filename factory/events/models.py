"""
STAC catalogs, the items fetched from them, and the rules that turn new items
into automatic notebook runs.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib.gis.db import models as gis_models
from django.contrib.postgres.fields import ArrayField
from django.contrib.postgres.indexes import GinIndex
from django.db import models
from django.urls import reverse


class CatalogProfile(models.TextChoices):
    GENERIC = "generic", "Generic STAC"
    MONTANDON = "montandon", "Montandon (monty:* extension)"


class StacCatalog(models.Model):
    """A STAC API that items can be picked from, polled and written back to."""

    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=80, unique=True)
    url = models.URLField(
        max_length=400, help_text="STAC API root, e.g. https://earth-search.aws.element84.com/v1"
    )
    api_key = models.TextField(
        blank=True,
        help_text="Sent as 'Authorization: Bearer <key>' when set (Montandon tokens are long JWTs)",
    )
    profile = models.CharField(
        max_length=20,
        choices=CatalogProfile.choices,
        default=CatalogProfile.GENERIC,
        help_text="How extra metadata (countries, correlation id) is read from items",
    )
    writeback_enabled = models.BooleanField(
        default=False, help_text="Notebook-produced STAC collections may be written to this catalog"
    )
    is_active = models.BooleanField(default=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    def get_absolute_url(self) -> str:
        return reverse("web:catalog-edit", args=[self.slug])

    @property
    def base_url(self) -> str:
        return self.url.rstrip("/")

    @property
    def is_montandon(self) -> bool:
        return self.profile == CatalogProfile.MONTANDON

    def item_url(self, collection: str, item_id: str) -> str:
        return f"{self.base_url}/collections/{collection}/items/{item_id}"

    def client(self):
        from .stac_client import StacClient

        return StacClient.for_catalog(self)


class StacItem(models.Model):
    """A STAC item fetched from a catalog; runs reference it and rules fire on it."""

    catalog = models.ForeignKey(StacCatalog, on_delete=models.PROTECT, related_name="items")
    item_id = models.CharField(max_length=200)
    collection = models.CharField(max_length=120, db_index=True)
    correlation_id = models.CharField(
        max_length=200, blank=True, db_index=True, help_text="monty:corr_id (Montandon profile)"
    )
    title = models.CharField(max_length=300, blank=True)
    description = models.TextField(blank=True)
    roles = models.JSONField(default=list, blank=True)
    facet_options = models.ManyToManyField(
        "catalog.FacetOption",
        blank=True,
        related_name="stac_items",
        help_text="Derived from the item's properties via each filter's STAC property",
    )
    country_codes = models.JSONField(default=list, blank=True)
    source = models.CharField(max_length=120, blank=True)
    occurred_at = models.DateTimeField(null=True, blank=True, db_index=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    item_updated_at = models.DateTimeField(null=True, blank=True)
    bbox = models.JSONField(null=True, blank=True)
    geom = gis_models.GeometryField(srid=4326, null=True, blank=True)
    raw = models.JSONField(default=dict, blank=True, help_text="The STAC item as fetched")
    event_refs = ArrayField(
        models.CharField(max_length=250),
        default=list,
        blank=True,
        help_text="IDs naming the disaster, e.g. glide:EQ-2025-000043-MMR (see factory.events.refs)",
    )
    first_seen_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-occurred_at", "-first_seen_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["catalog", "collection", "item_id"], name="uniq_stacitem_catalog_collection_item"
            )
        ]
        indexes = [GinIndex(fields=["event_refs"], name="stacitem_event_refs_gin")]

    def __str__(self) -> str:
        return self.title or self.item_id

    def get_absolute_url(self) -> str:
        return reverse("web:stac-item-detail", args=[self.pk])

    @property
    def countries_display(self) -> str:
        return ", ".join(self.country_codes or []) or "—"

    @property
    def stac_url(self) -> str:
        return self.catalog.item_url(self.collection, self.item_id)

    @property
    def facet_groups(self):
        from factory.catalog.facets import group_options

        return group_options(self.facet_options.all())

    @property
    def short_ref(self) -> str:
        return self.correlation_id or self.item_id

    def as_stac_item(self) -> dict:
        props = {
            "title": self.title,
            "datetime": self.occurred_at.isoformat() if self.occurred_at else None,
            "roles": self.roles,
        }
        if self.catalog.is_montandon:
            props.update(
                {
                    "monty:corr_id": self.correlation_id,
                    "monty:country_codes": self.country_codes,
                }
            )
        return {
            "type": "Feature",
            "stac_version": "1.0.0",
            "id": self.item_id,
            "collection": self.collection,
            "bbox": self.bbox,
            "geometry": self.raw.get("geometry"),
            "properties": props,
            "links": [],
            "assets": {},
        }


class TriggerRule(models.Model):
    """Maps new items in a catalog onto a template + parameters (Use Cases 2 and 3)."""

    name = models.CharField(max_length=120)
    enabled = models.BooleanField(default=True)
    catalog = models.ForeignKey(StacCatalog, on_delete=models.CASCADE, related_name="trigger_rules")
    notebook = models.ForeignKey(
        "notebooks.AnalysisNotebook", on_delete=models.CASCADE, related_name="trigger_rules"
    )
    collections = models.JSONField(
        default=list, help_text="STAC collections to watch, e.g. ['reference-events']"
    )
    search_filter = models.JSONField(
        default=dict, blank=True, help_text="Optional CQL2-JSON filter added to the search body"
    )
    roles = models.JSONField(
        default=list, blank=True, help_text="Only items with one of these roles (empty = any)"
    )
    country_codes = models.JSONField(
        default=list,
        blank=True,
        help_text="Only these ISO3 countries (empty = any). Needs a catalog profile that extracts countries.",
    )
    facet_options = models.ManyToManyField(
        "catalog.FacetOption",
        blank=True,
        related_name="trigger_rules",
        help_text="Only items tagged with these options: any option within a filter, every filter listed",
    )
    parameter_template = models.JSONField(
        default=dict,
        blank=True,
        help_text="Parameters for the run. String values may use Jinja, e.g. {{ item.correlation_id }}.",
    )
    area_level = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Fan out over all areas at this admin level in the item's countries; null = country-level run",
    )
    rerun_on_update = models.BooleanField(default=False, help_text="Re-run when the STAC item is updated")
    notify_emails = models.JSONField(default=list, blank=True)
    last_triggered_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class PollerState(models.Model):
    """Bookkeeping for the poller, one row per watched (catalog, collection)."""

    catalog = models.ForeignKey(StacCatalog, on_delete=models.CASCADE, related_name="poller_states")
    collection = models.CharField(max_length=120)
    last_polled_at = models.DateTimeField(null=True, blank=True)
    last_item_datetime = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    items_seen = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["catalog", "collection"], name="uniq_pollerstate_catalog_collection"
            )
        ]

    def __str__(self) -> str:
        label = f"{self.catalog_id}:{self.collection}"
        return f"{label} @ {self.last_item_datetime:%Y-%m-%d %H:%M}" if self.last_item_datetime else label


class ItemRuleLink(models.Model):
    """Which rule fired for which item (so we do not fire twice)."""

    item = models.ForeignKey(StacItem, on_delete=models.CASCADE, related_name="rule_links")
    rule = models.ForeignKey(TriggerRule, on_delete=models.CASCADE, related_name="item_links")
    item_updated_at = models.DateTimeField(null=True, blank=True)
    fired_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["item", "rule"], name="uniq_item_rule")]

    def __str__(self) -> str:
        return f"{self.rule_id} → {self.item_id}"


class LinkVerdict(models.TextChoices):
    SAME = "same", "Same disaster"
    DIFFERENT = "different", "Not the same disaster"


class EventLinkOverride(models.Model):
    """A staff correction to the automatic grouping of a disaster's items.

    `disaster_ref` is any ref of the disaster as it was shown (usually its key);
    `item_ref` is the primary ref of the item that was confirmed or removed. A
    verdict applies whenever the disaster being built carries `disaster_ref`.
    """

    catalog = models.ForeignKey(StacCatalog, on_delete=models.CASCADE, related_name="link_overrides")
    disaster_ref = models.CharField(max_length=250, db_index=True)
    item_ref = models.CharField(max_length=250, db_index=True)
    verdict = models.CharField(max_length=10, choices=LinkVerdict.choices)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["catalog", "disaster_ref", "item_ref"], name="uniq_eventlinkoverride"
            )
        ]

    def __str__(self) -> str:
        return f"{self.item_ref} {self.get_verdict_display().lower()} as {self.disaster_ref}"
