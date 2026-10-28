"""
The notebook index: templates (AnalysisNotebook) and their executions (NotebookRun).

Only light metadata lives here. Notebook content and rendered HTML live in the
template repository and in object storage respectively.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone


class SourceKind(models.TextChoices):
    LOCAL = "local", "Local templates directory"
    GIT = "git", "Git repository"
    BUNDLE = "bundle", "Uploaded bundle"


# Fields a TemplateVersion freezes; the template keeps a copy of its current version's values.
SNAPSHOT_FIELDS = (
    "notebook_path",
    "source_repo",
    "source_ref",
    "parameter_schema",
    "requires_area",
    "area_levels",
    "requires_stac_item",
    "stac_hints",
    "supports_writeback",
    "estimated_runtime_minutes",
)


class AnalysisNotebook(models.Model):
    """A notebook template: a recipe that declares its parameters."""

    slug = models.SlugField(max_length=80, unique=True)
    title = models.CharField(max_length=200)
    summary = models.CharField(max_length=300, blank=True, help_text="One-line description for cards")
    description = models.TextField(blank=True, help_text="Markdown allowed")
    facet_options = models.ManyToManyField(
        "catalog.FacetOption",
        blank=True,
        related_name="notebooks",
        help_text="Filter options this template applies to (e.g. its hazards and use case)",
    )

    source_kind = models.CharField(max_length=10, choices=SourceKind.choices, default=SourceKind.LOCAL)
    source_repo = models.URLField(blank=True, help_text="Git URL when source_kind=git")
    source_ref = models.CharField(max_length=120, blank=True, help_text="Branch, tag or commit")
    notebook_path = models.CharField(
        max_length=300,
        help_text="Path to the .ipynb relative to the templates dir (local), repo root (git) or bundle (uploaded)",
    )
    version = models.CharField(max_length=40, blank=True)

    parameter_schema = models.JSONField(
        default=dict,
        blank=True,
        help_text="JSON Schema (object) describing the user-facing parameters",
    )
    requires_area = models.BooleanField(default=True, help_text="Run needs a target admin area")
    area_levels = models.JSONField(
        default=list, blank=True, help_text="Allowed admin levels, e.g. [1, 2]; empty = any"
    )
    requires_stac_item = models.BooleanField(default=False, help_text="Run needs a STAC item")
    stac_hints = models.JSONField(
        default=dict,
        blank=True,
        help_text="Soft picker hints: {profile, collections, roles}. Never enforced.",
    )
    supports_writeback = models.BooleanField(
        default=False, help_text="Notebook produces a STAC collection to write back to the item's catalog"
    )
    estimated_runtime_minutes = models.PositiveIntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    current_version = models.ForeignKey(
        "TemplateVersion",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
        help_text="The version new runs use; its snapshot is copied onto this template",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["title"]

    def __str__(self) -> str:
        return self.title

    def get_absolute_url(self) -> str:
        return reverse("web:template-detail", args=[self.slug])

    @property
    def facet_groups(self):
        """[(facet, [options])] for pills; prefetch `facet_options__facet` when listing."""
        from factory.catalog.facets import group_options

        return group_options(self.facet_options.all())

    @property
    def parameter_count(self) -> int:
        return len((self.parameter_schema or {}).get("properties", {}))

    @property
    def last_run(self) -> NotebookRun | None:
        return self.runs.order_by("-created_at").first()

    def snapshot(self) -> dict:
        """The current values of the versioned fields (plus the free-text version as `label`)."""
        return {**{f: getattr(self, f) for f in SNAPSHOT_FIELDS}, "label": self.version}

    def make_current(self, version: TemplateVersion) -> None:
        """Point at `version` and copy its snapshot onto the template (the one write path for versioned fields)."""
        for f in SNAPSHOT_FIELDS:
            setattr(self, f, getattr(version, f))
        self.version = version.label
        if version.archive_key:
            self.source_kind = SourceKind.BUNDLE
        elif version.source_repo:
            self.source_kind = SourceKind.GIT
        else:
            self.source_kind = SourceKind.LOCAL
        self.current_version = version
        self.save()


class TemplateVersion(models.Model):
    """An immutable snapshot of a template: its code bundle plus the schema and inputs that match it."""

    notebook = models.ForeignKey(AnalysisNotebook, on_delete=models.CASCADE, related_name="versions")
    number = models.PositiveIntegerField()
    label = models.CharField(max_length=40, blank=True, help_text="Free-text version, e.g. 0.3")
    note = models.CharField(max_length=300, blank=True, help_text="What changed")

    archive_key = models.CharField(
        max_length=300, blank=True, help_text="Bundle zip in the published storage; empty for local / git"
    )
    sha256 = models.CharField(max_length=64, blank=True, help_text="Hash of the bundle's files")
    files = models.JSONField(default=list, blank=True, help_text="Paths in the bundle")

    notebook_path = models.CharField(max_length=300)
    source_repo = models.URLField(blank=True)
    source_ref = models.CharField(max_length=120, blank=True)
    parameter_schema = models.JSONField(default=dict, blank=True)
    requires_area = models.BooleanField(default=True)
    area_levels = models.JSONField(default=list, blank=True)
    requires_stac_item = models.BooleanField(default=False)
    stac_hints = models.JSONField(default=dict, blank=True)
    supports_writeback = models.BooleanField(default=False)
    estimated_runtime_minutes = models.PositiveIntegerField(null=True, blank=True)

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["notebook", "-number"]
        constraints = [
            models.UniqueConstraint(fields=["notebook", "number"], name="uniq_templateversion_number")
        ]

    def __str__(self) -> str:
        return f"{self.notebook.slug} v{self.number}" + (f" ({self.label})" if self.label else "")

    @property
    def is_current(self) -> bool:
        return self.notebook.current_version_id == self.pk

    @property
    def is_bundle(self) -> bool:
        return bool(self.archive_key)

    def snapshot(self) -> dict:
        return {**{f: getattr(self, f) for f in SNAPSHOT_FIELDS}, "label": self.label}


class RunStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    FETCHING = "fetching", "Fetching inputs"
    EXECUTING = "executing", "Executing notebook"
    RENDERING = "rendering", "Rendering HTML"
    PUBLISHING = "publishing", "Publishing"
    PUBLISHED = "published", "Published"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"


TERMINAL_STATUSES = {RunStatus.PUBLISHED, RunStatus.FAILED, RunStatus.CANCELLED}
STAGE_ORDER = [
    RunStatus.QUEUED,
    RunStatus.FETCHING,
    RunStatus.EXECUTING,
    RunStatus.RENDERING,
    RunStatus.PUBLISHING,
]


class TriggerKind(models.TextChoices):
    MANUAL = "manual", "Manual (UI)"
    API = "api", "API"
    AUTO = "auto", "Automatic (trigger rule)"
    CLI = "cli", "Command line"
    UPLOAD = "upload", "Uploaded"


class WritebackStatus(models.TextChoices):
    NONE = "none", "Not applicable"
    PENDING = "pending", "Pending"
    DONE = "done", "Written back"
    SKIPPED = "skipped", "Skipped"
    FAILED = "failed", "Failed"


class RunBatch(models.Model):
    """Groups runs created by one fan-out request (e.g. 'all Admin-2 in Nepal')."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    notebook = models.ForeignKey(AnalysisNotebook, on_delete=models.CASCADE, related_name="batches")
    description = models.CharField(max_length=300, blank=True)
    parameters = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    trigger = models.CharField(max_length=10, choices=TriggerKind.choices, default=TriggerKind.MANUAL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Batch {str(self.id)[:8]} · {self.notebook.title}"

    def get_absolute_url(self) -> str:
        return reverse("web:batch-detail", args=[self.id])

    def status_counts(self) -> dict[str, int]:
        counts = {s: 0 for s, _ in RunStatus.choices}
        for row in self.runs.values("status").annotate(n=models.Count("id")):
            counts[row["status"]] = row["n"]
        return counts


class NotebookRunQuerySet(models.QuerySet):
    def published(self):
        return self.filter(status=RunStatus.PUBLISHED)

    def active(self):
        return self.exclude(status__in=TERMINAL_STATUSES)

    def with_related(self):
        return self.select_related(
            "notebook",
            "template_version",
            "area",
            "area__country",
            "stac_item",
            "stac_item__catalog",
            "triggered_by",
            "batch",
        ).prefetch_related("run_facets__option__facet")


class NotebookRun(models.Model):
    """One execution of a template with concrete parameters, or an uploaded (already executed) notebook."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    notebook = models.ForeignKey(
        AnalysisNotebook,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="runs",
        help_text="Empty for uploaded notebooks",
    )
    template_version = models.ForeignKey(
        TemplateVersion,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="runs",
        help_text="The template version this run executes; empty for uploads and runs from before versioning",
    )
    batch = models.ForeignKey(RunBatch, null=True, blank=True, on_delete=models.SET_NULL, related_name="runs")
    status = models.CharField(
        max_length=12, choices=RunStatus.choices, default=RunStatus.QUEUED, db_index=True
    )
    parameters = models.JSONField(default=dict, blank=True, help_text="User-facing parameters (validated)")

    display_title = models.CharField(
        max_length=200, blank=True, help_text="Overrides the title built from the template and target"
    )
    summary = models.CharField(max_length=300, blank=True, help_text="One-line description for cards")
    description = models.TextField(blank=True, help_text="Markdown allowed")
    upload_path = models.CharField(
        max_length=300, blank=True, help_text="Uploads: path of the notebook inside source.zip"
    )

    area = models.ForeignKey(
        "catalog.AdminArea", null=True, blank=True, on_delete=models.SET_NULL, related_name="runs"
    )
    stac_item = models.ForeignKey(
        "events.StacItem", null=True, blank=True, on_delete=models.SET_NULL, related_name="runs"
    )

    facet_options = models.ManyToManyField(
        "catalog.FacetOption", through="RunFacet", blank=True, related_name="runs"
    )

    trigger = models.CharField(max_length=10, choices=TriggerKind.choices, default=TriggerKind.MANUAL)
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="notebook_runs",
    )
    trigger_rule = models.ForeignKey(
        "events.TriggerRule", null=True, blank=True, on_delete=models.SET_NULL, related_name="runs"
    )
    notify_emails = models.JSONField(default=list, blank=True)

    celery_task_id = models.CharField(max_length=64, blank=True)
    progress = models.JSONField(
        default=dict, blank=True, help_text="{'cell': 8, 'total': 23, 'message': ...}"
    )
    error = models.TextField(blank=True)

    output_prefix = models.CharField(max_length=300, blank=True, help_text="Storage key prefix for this run")
    output_url = models.URLField(max_length=600, blank=True, help_text="Published HTML entry point")
    notebook_url = models.URLField(max_length=600, blank=True, help_text="Executed .ipynb for download")
    artifacts = models.JSONField(default=dict, blank=True, help_text="Other published files: {name: url}")

    writeback_status = models.CharField(
        max_length=10, choices=WritebackStatus.choices, default=WritebackStatus.NONE
    )
    writeback_ref = models.CharField(
        max_length=400, blank=True, help_text="Collection id written to the item's catalog"
    )
    writeback_error = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    objects = NotebookRunQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(notebook__isnull=False) | models.Q(trigger="upload"),
                name="run_has_notebook_unless_upload",
            )
        ]

    def __str__(self) -> str:
        return f"{self.title} · {self.short_id}"

    def get_absolute_url(self) -> str:
        return reverse("web:run-detail", args=[self.id])

    # -- presentation helpers ------------------------------------------------
    @property
    def short_id(self) -> str:
        return str(self.id)[:8]

    @property
    def is_upload(self) -> bool:
        return self.notebook_id is None

    @property
    def title(self) -> str:
        if self.display_title:
            return self.display_title
        if self.notebook_id is None:
            return "Uploaded notebook"
        bits = [self.notebook.title]
        if self.area_id:
            bits.append(self.area.name)
        elif self.stac_item_id:
            bits.append(str(self.stac_item))
        return " · ".join(bits)

    @property
    def subtitle(self) -> str:
        bits = []
        if self.area_id:
            bits.append(f"{self.area.country.name} · {self.area.display_level}")
        elif self.stac_item_id:
            bits.append(
                self.stac_item.countries_display
                if self.stac_item.country_codes
                else self.stac_item.collection
            )
        bits.extend(o.label for _, options in self.facet_groups for o in options)
        return " · ".join(bits)

    @property
    def facet_groups(self):
        """[(facet, [options])] for pills; `with_related()` prefetches what this needs."""
        from factory.catalog.facets import group_options

        return group_options(rf.option for rf in self.run_facets.all())

    @property
    def filter_values(self) -> dict[str, list[str]]:
        """{facet_key: [option_key]} as injected into notebooks."""
        from factory.catalog.facets import option_keys

        return option_keys(rf.option for rf in self.run_facets.all())

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    @property
    def is_running(self) -> bool:
        return self.status in {
            RunStatus.FETCHING,
            RunStatus.EXECUTING,
            RunStatus.RENDERING,
            RunStatus.PUBLISHING,
        }

    @property
    def duration(self):
        if self.started_at:
            end = self.finished_at or timezone.now()
            return end - self.started_at
        return None

    @property
    def stage_order(self) -> list[str]:
        """Uploads are already executed, so they skip that stage."""
        if self.is_upload:
            return [s for s in STAGE_ORDER if s != RunStatus.EXECUTING]
        return STAGE_ORDER

    @property
    def stage_index(self) -> int:
        order = self.stage_order
        if self.status == RunStatus.PUBLISHED:
            return len(order)
        try:
            return order.index(RunStatus(self.status))
        except ValueError:
            return -1

    def stages(self) -> list[dict]:
        """Stepper view-model: [{key, label, state: done|active|pending|failed}]."""
        failed_at = None
        if self.status == RunStatus.FAILED:
            failed_at = (self.progress or {}).get("failed_stage")
        out = []
        for i, stage in enumerate(self.stage_order):
            if failed_at and stage == failed_at:
                state = "failed"
            elif i < self.stage_index:
                state = "done"
            elif i == self.stage_index and not self.is_terminal:
                state = "active"
            elif failed_at:
                state = "pending"
            else:
                state = "done" if self.status == RunStatus.PUBLISHED else "pending"
            label = {
                RunStatus.QUEUED: "Queued",
                RunStatus.FETCHING: "Unpacking upload" if self.is_upload else "Fetching inputs",
                RunStatus.EXECUTING: "Executing notebook",
                RunStatus.RENDERING: "Rendering HTML",
                RunStatus.PUBLISHING: "Publishing to storage",
            }[stage]
            out.append({"key": stage, "label": label, "state": state})
        return out

    def parameter_summary(self) -> str:
        schema_props = (self.notebook.parameter_schema or {}).get("properties", {}) if self.notebook else {}
        parts = []
        for key, value in (self.parameters or {}).items():
            title = schema_props.get(key, {}).get("title", key)
            if isinstance(value, list):
                value = ", ".join(str(v) for v in value)
            parts.append(f"{title}: {value}")
        return " · ".join(parts)

    # -- mutation helpers ----------------------------------------------------
    def set_status(self, status: str, message: str | None = None, **progress) -> None:
        self.status = status
        now = timezone.now()
        if status == RunStatus.FETCHING and not self.started_at:
            self.started_at = now
        if status in TERMINAL_STATUSES:
            self.finished_at = now
        if message is not None or progress:
            self.progress = {**(self.progress or {}), **progress}
            if message is not None:
                self.progress["message"] = message
        self.save(update_fields=["status", "started_at", "finished_at", "progress", "updated_at"])

    def log(self, message: str, level: str = "info") -> None:
        RunLogEntry.objects.create(run=self, message=message[:4000], level=level)


class FacetSource(models.TextChoices):
    PARAMETER = "parameter", "Template parameter"
    TEMPLATE = "template", "Template"
    STAC_ITEM = "stac_item", "STAC item"
    MANUAL = "manual", "Set by a user"


class RunFacet(models.Model):
    """A filter option on a run, and where it came from (so derived tags can be recomputed)."""

    run = models.ForeignKey(NotebookRun, on_delete=models.CASCADE, related_name="run_facets")
    option = models.ForeignKey("catalog.FacetOption", on_delete=models.CASCADE, related_name="run_facets")
    source = models.CharField(max_length=10, choices=FacetSource.choices)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["run", "option"], name="uniq_runfacet_run_option")]

    def __str__(self) -> str:
        return f"{self.run_id} · {self.option} ({self.source})"


class RunLogEntry(models.Model):
    run = models.ForeignKey(NotebookRun, on_delete=models.CASCADE, related_name="log_entries")
    created_at = models.DateTimeField(auto_now_add=True)
    level = models.CharField(max_length=10, default="info")
    message = models.TextField()

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name_plural = "run log entries"

    def __str__(self) -> str:
        return f"[{self.created_at:%H:%M:%S}] {self.message[:60]}"
