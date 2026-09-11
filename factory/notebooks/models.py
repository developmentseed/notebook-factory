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


class UseCase(models.TextChoices):
    UC1 = "uc1", "Risk exposure"
    UC2 = "uc2", "Impact estimation"
    UC3 = "uc3", "Response prioritisation"
    OTHER = "other", "Other"


class SourceKind(models.TextChoices):
    LOCAL = "local", "Local templates directory"
    GIT = "git", "Git repository"


class AnalysisNotebook(models.Model):
    """A notebook template: a recipe that declares its parameters."""

    slug = models.SlugField(max_length=80, unique=True)
    title = models.CharField(max_length=200)
    summary = models.CharField(max_length=300, blank=True, help_text="One-line description for cards")
    description = models.TextField(blank=True, help_text="Markdown allowed")
    use_case = models.CharField(max_length=10, choices=UseCase.choices, default=UseCase.OTHER)
    hazards = models.ManyToManyField("catalog.Hazard", blank=True, related_name="notebooks")

    source_kind = models.CharField(max_length=10, choices=SourceKind.choices, default=SourceKind.LOCAL)
    source_repo = models.URLField(blank=True, help_text="Git URL when source_kind=git")
    source_ref = models.CharField(max_length=120, blank=True, help_text="Branch, tag or commit")
    notebook_path = models.CharField(
        max_length=300,
        help_text="Path to the .ipynb relative to the templates dir (local) or repo root (git)",
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
    requires_event = models.BooleanField(default=False, help_text="Run needs a Montandon event")
    supports_writeback = models.BooleanField(
        default=False, help_text="Notebook produces a STAC collection to write back"
    )
    estimated_runtime_minutes = models.PositiveIntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["use_case", "title"]

    def __str__(self) -> str:
        return self.title

    def get_absolute_url(self) -> str:
        return reverse("web:template-detail", args=[self.slug])

    @property
    def parameter_count(self) -> int:
        return len((self.parameter_schema or {}).get("properties", {}))

    @property
    def last_run(self) -> NotebookRun | None:
        return self.runs.order_by("-created_at").first()


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
    AUTO = "auto", "Automatic (event)"
    CLI = "cli", "Command line"


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
            "notebook", "area", "area__country", "hazard", "event", "triggered_by", "batch"
        )


class NotebookRun(models.Model):
    """One execution of a template with concrete parameters."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    notebook = models.ForeignKey(AnalysisNotebook, on_delete=models.PROTECT, related_name="runs")
    batch = models.ForeignKey(RunBatch, null=True, blank=True, on_delete=models.SET_NULL, related_name="runs")
    status = models.CharField(
        max_length=12, choices=RunStatus.choices, default=RunStatus.QUEUED, db_index=True
    )
    parameters = models.JSONField(default=dict, blank=True, help_text="User-facing parameters (validated)")

    area = models.ForeignKey(
        "catalog.AdminArea", null=True, blank=True, on_delete=models.SET_NULL, related_name="runs"
    )
    hazard = models.ForeignKey(
        "catalog.Hazard", null=True, blank=True, on_delete=models.SET_NULL, related_name="runs"
    )
    event = models.ForeignKey(
        "events.Event", null=True, blank=True, on_delete=models.SET_NULL, related_name="runs"
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
        "events.EventTriggerRule", null=True, blank=True, on_delete=models.SET_NULL, related_name="runs"
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
        max_length=400, blank=True, help_text="Collection id / URL written to Montandon"
    )
    writeback_error = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    objects = NotebookRunQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.notebook.title} · {self.short_id}"

    def get_absolute_url(self) -> str:
        return reverse("web:run-detail", args=[self.id])

    # -- presentation helpers ------------------------------------------------
    @property
    def short_id(self) -> str:
        return str(self.id)[:8]

    @property
    def title(self) -> str:
        bits = [self.notebook.title]
        if self.area_id:
            bits.append(self.area.name)
        elif self.event_id:
            bits.append(self.event.title)
        return " · ".join(bits)

    @property
    def subtitle(self) -> str:
        bits = []
        if self.area_id:
            bits.append(f"{self.area.country.name} · {self.area.display_level}")
        elif self.event_id and self.event.country_codes:
            bits.append(", ".join(self.event.country_codes))
        if self.hazard_id:
            bits.append(self.hazard.label)
        return " · ".join(bits)

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
    def stage_index(self) -> int:
        if self.status == RunStatus.PUBLISHED:
            return len(STAGE_ORDER)
        try:
            return STAGE_ORDER.index(RunStatus(self.status))
        except ValueError:
            return -1

    def stages(self) -> list[dict]:
        """Stepper view-model: [{key, label, state: done|active|pending|failed}]."""
        failed_at = None
        if self.status == RunStatus.FAILED:
            failed_at = (self.progress or {}).get("failed_stage")
        out = []
        for i, stage in enumerate(STAGE_ORDER):
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
                RunStatus.FETCHING: "Fetching inputs",
                RunStatus.EXECUTING: "Executing notebook",
                RunStatus.RENDERING: "Rendering HTML",
                RunStatus.PUBLISHING: "Publishing to storage",
            }[stage]
            out.append({"key": stage, "label": label, "state": state})
        return out

    def parameter_summary(self) -> str:
        schema_props = (self.notebook.parameter_schema or {}).get("properties", {})
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
