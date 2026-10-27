from django.contrib import admin, messages
from django.contrib.gis import admin as gis_admin
from django.utils import timezone

from .models import ItemRuleLink, PollerState, StacCatalog, StacItem, TriggerRule
from .stac_client import StacClientError


@admin.register(StacCatalog)
class StacCatalogAdmin(admin.ModelAdmin):
    list_display = ("name", "url", "profile", "has_key", "writeback_enabled", "is_active", "last_checked_at")
    list_filter = ("profile", "is_active", "writeback_enabled")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("last_checked_at", "last_error", "created_at", "updated_at")
    actions = ["test_connection"]

    @admin.display(boolean=True, description="API key")
    def has_key(self, obj):
        return bool(obj.api_key)

    @admin.action(description="Test connection (list collections)")
    def test_connection(self, request, queryset):
        for catalog in queryset:
            try:
                with catalog.client() as client:
                    n = len(client.collections())
                catalog.last_error = ""
                self.message_user(request, f"{catalog}: OK, {n} collections")
            except StacClientError as exc:
                catalog.last_error = str(exc)
                self.message_user(request, f"{catalog}: {exc}", level=messages.ERROR)
            catalog.last_checked_at = timezone.now()
            catalog.save(update_fields=["last_checked_at", "last_error", "updated_at"])


@admin.register(StacItem)
class StacItemAdmin(gis_admin.GISModelAdmin):
    list_display = (
        "title",
        "catalog",
        "collection",
        "correlation_id",
        "hazard",
        "countries_display",
        "occurred_at",
        "first_seen_at",
    )
    list_filter = ("catalog", "collection", "hazard")
    search_fields = ("title", "item_id", "correlation_id")
    readonly_fields = ("first_seen_at", "updated_at")
    date_hierarchy = "occurred_at"


@admin.register(TriggerRule)
class TriggerRuleAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "enabled",
        "catalog",
        "notebook",
        "collections",
        "area_level",
        "rerun_on_update",
        "last_triggered_at",
    )
    list_filter = ("enabled", "catalog", "notebook")
    filter_horizontal = ("hazards",)


@admin.register(PollerState)
class PollerStateAdmin(admin.ModelAdmin):
    list_display = (
        "catalog",
        "collection",
        "last_polled_at",
        "last_item_datetime",
        "items_seen",
        "last_error",
    )
    list_filter = ("catalog",)
    readonly_fields = ("items_seen",)


@admin.register(ItemRuleLink)
class ItemRuleLinkAdmin(admin.ModelAdmin):
    list_display = ("item", "rule", "fired_at")
    raw_id_fields = ("item",)
