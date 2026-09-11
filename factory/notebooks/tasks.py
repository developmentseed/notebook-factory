import logging

from celery import shared_task
from django.db import close_old_connections

from .pipeline.runner import cleanup_workdirs as _cleanup
from .pipeline.runner import run_pipeline

log = logging.getLogger(__name__)


@shared_task(name="factory.notebooks.tasks.execute_run", bind=True)
def execute_run(self, run_id: str):
    close_old_connections()
    run = run_pipeline(run_id)
    return {"run_id": str(run.id), "status": run.status, "output_url": run.output_url}


@shared_task(name="factory.notebooks.tasks.cleanup_workdirs")
def cleanup_workdirs():
    from django.conf import settings

    from .services import fail_stale_runs

    removed = _cleanup()
    failed = fail_stale_runs(settings.NOTEBOOK_FACTORY["STALE_RUN_HOURS"])
    log.info("cleanup: removed %d work directories, failed %d stale runs", removed, failed)
    return {"workdirs_removed": removed, "stale_failed": failed}
