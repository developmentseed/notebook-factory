"""Create and enqueue runs. Used by the web UI, the API, the CLI and the event poller."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings
from django.db import transaction

from factory.catalog.models import AdminArea, Hazard

from . import parameters as P
from .models import AnalysisNotebook, NotebookRun, RunBatch, RunStatus, TriggerKind

log = logging.getLogger(__name__)


class RunRequestError(ValueError):
    pass


def _resolve_hazard(notebook: AnalysisNotebook, params: dict, hazard: Hazard | None) -> Hazard | None:
    if hazard:
        return hazard
    key = params.get("hazard")
    if key:
        h = Hazard.objects.filter(key=key).first()
        if h:
            return h
    if notebook.hazards.count() == 1:
        return notebook.hazards.first()
    return None


def create_run(
    notebook: AnalysisNotebook,
    parameters: dict | None = None,
    *,
    area: AdminArea | None = None,
    event=None,
    hazard: Hazard | None = None,
    user=None,
    trigger: str = TriggerKind.MANUAL,
    trigger_rule=None,
    notify_emails: list[str] | None = None,
    batch: RunBatch | None = None,
    enqueue: bool = True,
) -> NotebookRun:
    if not notebook.is_active:
        raise RunRequestError("This template is not active.")
    if notebook.requires_area and area is None:
        raise RunRequestError("This template needs a target admin area.")
    if area is not None and notebook.area_levels and area.level not in notebook.area_levels:
        raise RunRequestError(f"This template accepts admin levels {notebook.area_levels}, not {area.level}.")
    if notebook.requires_event and event is None:
        raise RunRequestError("This template needs a Montandon event.")
    params = P.validate(notebook.parameter_schema, parameters or {})

    emails = [e.strip() for e in (notify_emails or []) if e and e.strip()]
    if user is not None and getattr(user, "email", "") and user.email not in emails:
        emails.append(user.email)

    with transaction.atomic():
        run = NotebookRun.objects.create(
            notebook=notebook,
            batch=batch,
            parameters=params,
            area=area,
            event=event,
            hazard=_resolve_hazard(notebook, params, hazard),
            triggered_by=user if getattr(user, "is_authenticated", False) else None,
            trigger=trigger,
            trigger_rule=trigger_rule,
            notify_emails=emails,
        )
        run.log("run created and queued")
        if enqueue:
            transaction.on_commit(lambda: enqueue_run(run))
    return run


def create_batch(
    notebook: AnalysisNotebook,
    parameters: dict | None,
    areas,
    *,
    description: str = "",
    user=None,
    trigger: str = TriggerKind.MANUAL,
    notify_emails: list[str] | None = None,
    event=None,
    trigger_rule=None,
) -> RunBatch:
    """Fan a single request out across many areas (one run per area)."""
    areas = list(areas)
    if not areas:
        raise RunRequestError("No areas selected.")
    with transaction.atomic():
        batch = RunBatch.objects.create(
            notebook=notebook,
            description=description or f"{len(areas)} areas",
            parameters=parameters or {},
            created_by=user if getattr(user, "is_authenticated", False) else None,
            trigger=trigger,
        )
        for area in areas:
            create_run(
                notebook,
                parameters,
                area=area,
                event=event,
                user=user,
                trigger=trigger,
                trigger_rule=trigger_rule,
                notify_emails=notify_emails,
                batch=batch,
            )
    return batch


_eager_pool: ThreadPoolExecutor | None = None


def _eager_executor() -> ThreadPoolExecutor:
    """Bounded in-process executor for broker-less ("eager") mode."""
    global _eager_pool
    if _eager_pool is None:
        _eager_pool = ThreadPoolExecutor(
            max_workers=settings.NOTEBOOK_FACTORY["EAGER_WORKERS"], thread_name_prefix="notebook-run"
        )
    return _eager_pool


def enqueue_run(run: NotebookRun) -> None:
    from .pipeline.runner import run_pipeline
    from .tasks import execute_run

    if settings.CELERY_TASK_ALWAYS_EAGER:
        # No broker: run in a background thread so the request returns immediately.
        # Threads die with the process, so a dev-server restart can leave runs stuck;
        # `manage.py stale_runs --requeue` recovers them.
        _eager_executor().submit(_threaded, run_pipeline, str(run.id))
        return
    result = execute_run.apply_async(args=[str(run.id)])
    NotebookRun.objects.filter(pk=run.pk).update(celery_task_id=result.id)


def _threaded(fn, run_id):
    from django.db import close_old_connections, connection

    try:
        close_old_connections()
        fn(run_id)
    except Exception:  # noqa: BLE001
        log.exception("eager run %s crashed", run_id)
    finally:
        connection.close()


def cancel_run(run: NotebookRun) -> bool:
    """Cancel a queued run. Running notebooks are revoked best-effort."""
    if run.is_terminal:
        return False
    if run.celery_task_id and not settings.CELERY_TASK_ALWAYS_EAGER:
        from config.celery import app

        app.control.revoke(run.celery_task_id, terminate=run.is_running, signal="SIGTERM")
    run.set_status(RunStatus.CANCELLED, "Cancelled")
    run.log("cancelled by user")
    return True


def retry_run(run: NotebookRun, user=None) -> NotebookRun:
    """Create a fresh run with the same inputs."""
    return create_run(
        run.notebook,
        run.parameters,
        area=run.area,
        event=run.event,
        hazard=run.hazard,
        user=user or run.triggered_by,
        trigger=run.trigger,
        trigger_rule=run.trigger_rule,
        notify_emails=run.notify_emails,
        batch=run.batch,
    )


def stale_runs(max_age_hours: float):
    """Runs that are not terminal and have not been updated for a while (dead worker / restarted dev server)."""
    from datetime import timedelta

    from django.utils import timezone

    cutoff = timezone.now() - timedelta(hours=max_age_hours)
    return NotebookRun.objects.active().filter(updated_at__lt=cutoff)


def fail_stale_runs(max_age_hours: float) -> int:
    n = 0
    for run in stale_runs(max_age_hours):
        run.error = f"No progress for more than {max_age_hours:g} h; the worker probably died."
        run.save(update_fields=["error"])
        run.set_status(RunStatus.FAILED, "Stale", failed_stage=run.status)
        run.log("marked as failed: stale", level="error")
        n += 1
    return n
