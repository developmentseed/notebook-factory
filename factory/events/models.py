"""
Montandon events and the rules that turn them into automatic notebook runs.
"""

from __future__ import annotations

from django.contrib.gis.db import models as gis_models
from django.db import models
from django.urls import reverse


class Event(models.Model):
    """A Montandon STAC item (event / hazard / impact) that may trigger analyses."""

    stac_id = models.CharField(max_length=200)
    collection = models.CharField(max_length=120, db_index=True)
    monty_corr_id = models.CharField(max_length=200, blank=True, db_index=True)
    title = models.CharField(max_length=300, blank=True)
    description = models.TextField(blank=True)
    roles = models.JSONField(default=list, blank=True)
    hazard = models.ForeignKey(
        "catalog.Hazard", null=True, blank=True, on_delete=models.SET_NULL, related_name="events"
    )
    hazard_codes = models.JSONField(default=list, blank=True)
    country_codes = models.JSONField(default=list, blank=True)
    source = models.CharField(max_length=120, blank=True)
    occurred_at = models.DateTimeField(null=True, blank=True, db_index=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    item_updated_at = models.DateTimeField(null=True, blank=True)
    bbox = models.JSONField(null=True, blank=True)
    geom = gis_models.GeometryField(srid=4326, null=True, blank=True)
    raw = models.JSONField(default=dict, blank=True, help_text="The STAC item as fetched")
    first_seen_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-occurred_at", "-first_seen_at"]
        constraints = [
            models.UniqueConstraint(fields=["collection", "stac_id"], name="uniq_event_collection_item")
        ]

    def __str__(self) -> str:
        return self.title or self.stac_id

    def get_absolute_url(self) -> str:
        return reverse("web:event-detail", args=[self.pk])

    @property
    def countries_display(self) -> str:
        return ", ".join(self.country_codes or []) or "—"

    @property
    def stac_url(self) -> str:
        from django.conf import settings

        return (
            f"{settings.MONTANDON['STAC_URL'].rstrip('/')}/collections/{self.collection}/items/{self.stac_id}"
        )

    def as_stac_item(self) -> dict:
        return {
            "type": "Feature",
            "stac_version": "1.0.0",
            "id": self.stac_id,
            "collection": self.collection,
            "bbox": self.bbox,
            "geometry": self.raw.get("geometry"),
            "properties": {
                "title": self.title,
                "datetime": self.occurred_at.isoformat() if self.occurred_at else None,
                "monty:corr_id": self.monty_corr_id,
                "monty:hazard_codes": self.hazard_codes,
                "monty:country_codes": self.country_codes,
                "roles": self.roles,
            },
            "links": [],
            "assets": {},
        }


class EventTriggerRule(models.Model):
    """Maps incoming events onto a template + parameters (Use Cases 2 and 3)."""

    name = models.CharField(max_length=120)
    enabled = models.BooleanField(default=True)
    notebook = models.ForeignKey(
        "notebooks.AnalysisNotebook", on_delete=models.CASCADE, related_name="trigger_rules"
    )
    collections = models.JSONField(
        default=list, help_text="STAC collections to watch, e.g. ['reference-events']"
    )
    roles = models.JSONField(
        default=list, blank=True, help_text="Only items with one of these roles (empty = any)"
    )
    hazards = models.ManyToManyField(
        "catalog.Hazard", blank=True, help_text="Only these hazards (empty = any)"
    )
    country_codes = models.JSONField(
        default=list, blank=True, help_text="Only these ISO3 countries (empty = any)"
    )
    parameter_template = models.JSONField(
        default=dict,
        blank=True,
        help_text="Parameters for the run. String values may use Jinja, e.g. {{ event.monty_corr_id }}.",
    )
    area_level = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Fan out over all areas at this admin level in the event's countries; null = country-level run",
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
    """Bookkeeping for the Montandon poller, one row per watched collection."""

    collection = models.CharField(max_length=120, unique=True)
    last_polled_at = models.DateTimeField(null=True, blank=True)
    last_item_datetime = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    items_seen = models.PositiveIntegerField(default=0)

    def __str__(self) -> str:
        return (
            f"{self.collection} @ {self.last_item_datetime:%Y-%m-%d %H:%M}"
            if self.last_item_datetime
            else self.collection
        )


class EventRunLink(models.Model):
    """Which rule fired for which event (so we do not fire twice)."""

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="rule_links")
    rule = models.ForeignKey(EventTriggerRule, on_delete=models.CASCADE, related_name="event_links")
    item_updated_at = models.DateTimeField(null=True, blank=True)
    fired_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["event", "rule"], name="uniq_event_rule")]

    def __str__(self) -> str:
        return f"{self.rule_id} → {self.event_id}"
