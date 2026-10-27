from django.contrib import admin

from .models import AnalysisNotebook, NotebookRun, RunBatch, RunFacet, RunLogEntry


@admin.register(AnalysisNotebook)
class AnalysisNotebookAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "slug",
        "version",
        "source_kind",
        "requires_area",
        "requires_stac_item",
        "is_active",
    )
    list_filter = ("source_kind", "is_active", "facet_options")
    search_fields = ("title", "slug", "summary")
    filter_horizontal = ("facet_options",)
    prepopulated_fields = {"slug": ("title",)}


class RunFacetInline(admin.TabularInline):
    model = RunFacet
    extra = 0
    autocomplete_fields = ("option",)


class RunLogInline(admin.TabularInline):
    model = RunLogEntry
    extra = 0
    readonly_fields = ("created_at", "level", "message")
    can_delete = False


@admin.register(NotebookRun)
class NotebookRunAdmin(admin.ModelAdmin):
    list_display = (
        "short_id",
        "notebook",
        "status",
        "area",
        "stac_item",
        "trigger",
        "triggered_by",
        "created_at",
        "finished_at",
    )
    list_filter = ("status", "trigger", "notebook", "facet_options", "writeback_status")
    search_fields = (
        "id",
        "area__name",
        "stac_item__title",
        "stac_item__item_id",
        "stac_item__correlation_id",
    )
    readonly_fields = (
        "id",
        "created_at",
        "updated_at",
        "started_at",
        "finished_at",
        "celery_task_id",
        "progress",
        "output_url",
        "notebook_url",
        "artifacts",
    )
    raw_id_fields = ("area", "stac_item", "batch", "triggered_by")
    inlines = [RunFacetInline, RunLogInline]
    date_hierarchy = "created_at"


@admin.register(RunBatch)
class RunBatchAdmin(admin.ModelAdmin):
    list_display = ("id", "notebook", "description", "trigger", "created_by", "created_at")
    raw_id_fields = ("created_by",)
