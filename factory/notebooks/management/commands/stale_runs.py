from django.core.management.base import BaseCommand

from factory.notebooks.pipeline.runner import run_pipeline
from factory.notebooks.services import enqueue_run, fail_stale_runs, stale_runs


class Command(BaseCommand):
    help = "List, fail or requeue runs that stopped making progress (e.g. after a dev-server restart)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--older-than", type=float, default=0.5, help="Hours without progress (default 0.5)"
        )
        group = parser.add_mutually_exclusive_group()
        group.add_argument("--fail", action="store_true", help="Mark them failed")
        group.add_argument(
            "--requeue", action="store_true", help="Re-run them (inline when no broker is configured)"
        )

    def handle(self, *args, **opts):
        from django.conf import settings

        runs = list(stale_runs(opts["older_than"]))
        for run in runs:
            self.stdout.write(
                f"  {run.id}  {run.status:10s} {run.notebook.slug}  updated {run.updated_at:%Y-%m-%d %H:%M}"
            )
        if not runs:
            self.stdout.write("no stale runs")
            return
        if opts["fail"]:
            self.stdout.write(self.style.WARNING(f"{fail_stale_runs(opts['older_than'])} runs marked failed"))
        elif opts["requeue"]:
            for run in runs:
                run.set_status("queued", "Requeued", failed_stage=None, cell=0)
                run.log("requeued")
                if settings.CELERY_TASK_ALWAYS_EAGER:
                    run_pipeline(str(run.id))
                    run.refresh_from_db()
                    self.stdout.write(f"  {run.short_id} -> {run.status}")
                else:
                    enqueue_run(run)
            self.stdout.write(self.style.SUCCESS(f"{len(runs)} runs requeued"))
