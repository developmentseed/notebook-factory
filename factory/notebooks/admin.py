from django import forms
from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from .models import (
    SNAPSHOT_FIELDS,
    AnalysisNotebook,
    NotebookRun,
    RunBatch,
    RunFacet,
    RunLogEntry,
    TemplateSecret,
    TemplateVersion,
)


class TemplateVersionInline(admin.TabularInline):
    model = TemplateVersion
    extra = 0
    fields = ("number", "label", "note", "notebook_path", "archive_key", "created_by", "created_at")
    readonly_fields = fields
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(AnalysisNotebook)
class AnalysisNotebookAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "slug",
        "current_version",
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
    inlines = [TemplateVersionInline]

    def get_readonly_fields(self, request, obj=None):
        """Versioned fields of uploaded templates change through the editor, which records a version."""
        if obj is not None and obj.current_version_id:
            return ("editor_link", "current_version", "source_kind", "version", *SNAPSHOT_FIELDS)
        return ("editor_link", "current_version")

    @admin.display(description="Editor")
    def editor_link(self, obj):
        if obj is None or not obj.pk:
            return "—"
        return format_html(
            '<a href="{}">Edit parameters and versions in the app</a>',
            reverse("web:template-edit", args=[obj.slug]),
        )


@admin.register(TemplateVersion)
class TemplateVersionAdmin(admin.ModelAdmin):
    """Read-only: versions are immutable once runs use them; create them in the app or with sync_templates."""

    list_display = ("notebook", "number", "label", "note", "notebook_path", "created_by", "created_at")
    list_filter = ("notebook",)
    search_fields = ("notebook__slug", "label", "note", "sha256")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


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
        "display_title",
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


class TemplateSecretAdminForm(forms.ModelForm):
    value = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text="Never shown again. Leave blank to keep the current value.",
    )

    class Meta:
        model = TemplateSecret
        fields = ["name", "description", "value", "from_env", "templates"]

    def clean(self):
        data = super().clean()
        if not data.get("value") and not data.get("from_env") and self.instance.pk:
            data["value"] = self.instance.value
        if data.get("from_env"):
            data["value"] = ""
        return data


@admin.register(TemplateSecret)
class TemplateSecretAdmin(admin.ModelAdmin):
    """The value is write-only; templates get it only when they declare the name and are granted it."""

    form = TemplateSecretAdminForm
    list_display = ("name", "description", "from_env", "updated_at")
    search_fields = ("name", "description")
    filter_horizontal = ("templates",)
