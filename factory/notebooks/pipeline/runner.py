"""
The run pipeline: fetching → executing → rendering → publishing (→ write-back, notify).

`run_pipeline(run_id)` is what the Celery task (or the eager thread) calls.
"""

from __future__ import annotations

import logging
import shutil
import traceback

from django.conf import settings
from django.utils import timezone

from ..models import NotebookRun, RunStatus, WritebackStatus
from . import executor, inputs, publisher, renderer, sources
from .context import RunContext

log = logging.getLogger(__name__)


def run_pipeline(run_id: str) -> NotebookRun:
    run = NotebookRun.objects.with_related().get(pk=run_id)
    if run.status == RunStatus.CANCELLED:
        return run
    if run.is_terminal:
        log.info("Run %s already %s; skipping", run_id, run.status)
        return run

    ctx = RunContext.create(str(run.id))
    stage = RunStatus.FETCHING
    try:
        run.set_status(RunStatus.FETCHING, "Resolving template and preparing inputs", cell=0, total=0)
        run.log(f"worker picked up run {run.short_id}")
        sources.resolve_template(run.notebook, ctx)
        run.log(
            f"template: {run.notebook.slug} ({run.notebook.version or 'unversioned'}) from {run.notebook.get_source_kind_display().lower()}"
        )
        injected = inputs.prepare_inputs(run, ctx)
        if run.area_id:
            run.log(f"inputs: area {run.area.name} (L{run.area.level}) + subdivisions written to inputs/")
        if run.event_id:
            run.log(f"inputs: event {run.event.monty_corr_id or run.event.stac_id} written to inputs/")

        stage = RunStatus.EXECUTING
        run.set_status(RunStatus.EXECUTING, "Executing notebook with papermill")
        params = {**injected, **(run.parameters or {})}
        executor.execute(run, ctx, params)
        run.log("papermill: notebook executed")

        stage = RunStatus.RENDERING
        run.set_status(RunStatus.RENDERING, "Rendering static HTML with MyST")
        renderer.render(run, ctx, base_url=publisher.base_url_path(run))

        stage = RunStatus.PUBLISHING
        run.set_status(RunStatus.PUBLISHING, "Uploading to storage")
        result = publisher.publish(run, ctx)
        run.output_prefix = result["output_prefix"]
        run.output_url = result["output_url"]
        run.notebook_url = result["notebook_url"]
        run.artifacts = result["artifacts"]
        run.save(update_fields=["output_prefix", "output_url", "notebook_url", "artifacts", "updated_at"])

        if run.notebook.supports_writeback:
            _writeback(run, ctx)

        run.set_status(RunStatus.PUBLISHED, "Published", failed_stage=None)
        run.log(f"published: {run.output_url}")
    except Exception as exc:  # noqa: BLE001
        log.exception("Run %s failed during %s", run.id, stage)
        run.error = f"{type(exc).__name__}: {exc}"
        if settings.DEBUG:
            run.error += "\n\n" + traceback.format_exc()
        run.save(update_fields=["error", "updated_at"])
        run.set_status(RunStatus.FAILED, f"Failed while {stage}", failed_stage=stage)
        run.log(f"failed during {stage}: {exc}", level="error")
    finally:
        if not settings.NOTEBOOK_FACTORY["KEEP_WORKDIRS"]:
            shutil.rmtree(ctx.workdir, ignore_errors=True)

    _notify(run)
    return run


def _writeback(run: NotebookRun, ctx: RunContext) -> None:
    from factory.events.writeback import write_back

    run.writeback_status = WritebackStatus.PENDING
    run.save(update_fields=["writeback_status"])
    try:
        ref = write_back(run, ctx.outputs_dir)
        run.writeback_status = WritebackStatus.DONE if ref else WritebackStatus.SKIPPED
        run.writeback_ref = ref or ""
        run.log(f"write-back: {ref or 'skipped (nothing to write or disabled)'}")
    except Exception as exc:  # noqa: BLE001 - write-back failure must not fail the run
        log.exception("write-back failed for %s", run.id)
        run.writeback_status = WritebackStatus.FAILED
        run.writeback_error = f"{type(exc).__name__}: {exc}"
        run.log(f"write-back failed: {exc}", level="error")
    run.save(update_fields=["writeback_status", "writeback_ref", "writeback_error", "updated_at"])


def _notify(run: NotebookRun) -> None:
    try:
        from factory.notifications.services import notify_run_finished

        notify_run_finished(run)
    except Exception:  # noqa: BLE001
        log.exception("notification failed for %s", run.id)


def cleanup_workdirs(max_age_hours: int | None = None) -> int:
    """Delete stale work directories (kept for debugging) older than the configured age."""
    cfg = settings.NOTEBOOK_FACTORY
    max_age = max_age_hours or cfg["WORKDIR_MAX_AGE_HOURS"]
    base = cfg["WORK_DIR"]
    if not base.exists():
        return 0
    cutoff = timezone.now().timestamp() - max_age * 3600
    removed = 0
    for d in base.iterdir():
        if d.is_dir() and d.stat().st_mtime < cutoff:
            shutil.rmtree(d, ignore_errors=True)
            removed += 1
    return removed
