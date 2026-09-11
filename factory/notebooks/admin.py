from django.contrib import admin

from .models import AnalysisNotebook, NotebookRun, RunBatch, RunLogEntry


@admin.register(AnalysisNotebook)
class AnalysisNotebookAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "slug",
        "use_case",
        "version",
        "source_kind",
        "requires_area",
        "requires_event",
        "is_active",
    )
    list_filter = ("use_case", "source_kind", "is_active", "hazards")
    search_fields = ("title", "slug", "summary")
    filter_horizontal = ("hazards",)
    prepopulated_fields = {"slug": ("title",)}


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
        "hazard",
        "event",
        "trigger",
        "triggered_by",
        "created_at",
        "finished_at",
    )
    list_filter = ("status", "trigger", "notebook", "hazard", "writeback_status")
    search_fields = ("id", "area__name", "event__title", "event__monty_corr_id")
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
    raw_id_fields = ("area", "event", "batch", "triggered_by")
    inlines = [RunLogInline]
    date_hierarchy = "created_at"


@admin.register(RunBatch)
class RunBatchAdmin(admin.ModelAdmin):
    list_display = ("id", "notebook", "description", "trigger", "created_by", "created_at")
    raw_id_fields = ("created_by",)
