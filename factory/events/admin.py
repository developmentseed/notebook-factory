from django.contrib import admin
from django.contrib.gis import admin as gis_admin

from .models import Event, EventRunLink, EventTriggerRule, PollerState


@admin.register(Event)
class EventAdmin(gis_admin.GISModelAdmin):
    list_display = (
        "title",
        "collection",
        "monty_corr_id",
        "hazard",
        "countries_display",
        "occurred_at",
        "first_seen_at",
    )
    list_filter = ("collection", "hazard")
    search_fields = ("title", "stac_id", "monty_corr_id")
    readonly_fields = ("first_seen_at", "updated_at")
    date_hierarchy = "occurred_at"


@admin.register(EventTriggerRule)
class EventTriggerRuleAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "enabled",
        "notebook",
        "collections",
        "area_level",
        "rerun_on_update",
        "last_triggered_at",
    )
    list_filter = ("enabled", "notebook")
    filter_horizontal = ("hazards",)


@admin.register(PollerState)
class PollerStateAdmin(admin.ModelAdmin):
    list_display = ("collection", "last_polled_at", "last_item_datetime", "items_seen", "last_error")
    readonly_fields = ("items_seen",)


@admin.register(EventRunLink)
class EventRunLinkAdmin(admin.ModelAdmin):
    list_display = ("event", "rule", "fired_at")
    raw_id_fields = ("event",)
