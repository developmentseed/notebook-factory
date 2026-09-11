import json

from django.core.management.base import BaseCommand, CommandError

from factory.catalog.models import AdminArea
from factory.events.models import Event
from factory.notebooks.models import AnalysisNotebook, TriggerKind
from factory.notebooks.parameters import ParameterError
from factory.notebooks.pipeline.runner import run_pipeline
from factory.notebooks.services import RunRequestError, create_run


class Command(BaseCommand):
    help = "Run a template synchronously from the command line (no worker needed)."

    def add_arguments(self, parser):
        parser.add_argument("slug")
        parser.add_argument("--area", help="Admin area code (e.g. GADM GID) or numeric id")
        parser.add_argument("--event", help="Event id, STAC item id or monty:corr_id")
        parser.add_argument(
            "-p", "--param", action="append", default=[], help="key=value (JSON values allowed)"
        )
        parser.add_argument(
            "--async", dest="use_queue", action="store_true", help="Enqueue instead of running inline"
        )

    def handle(self, *args, **opts):
        try:
            nb = AnalysisNotebook.objects.get(slug=opts["slug"])
        except AnalysisNotebook.DoesNotExist as exc:
            raise CommandError(f"Unknown template {opts['slug']}") from exc
        area = None
        if opts["area"]:
            area = AdminArea.objects.filter(code=opts["area"]).first()
            if area is None and opts["area"].isdigit():
                area = AdminArea.objects.filter(pk=int(opts["area"])).first()
            if area is None:
                raise CommandError(f"Unknown area {opts['area']}")
        event = None
        if opts["event"]:
            ev = opts["event"]
            event = Event.objects.filter(stac_id=ev).first() or Event.objects.filter(monty_corr_id=ev).first()
            if event is None and ev.isdigit():
                event = Event.objects.filter(pk=int(ev)).first()
            if event is None:
                raise CommandError(f"Unknown event {ev}")
        params = {}
        for kv in opts["param"]:
            key, _, raw = kv.partition("=")
            try:
                params[key] = json.loads(raw)
            except json.JSONDecodeError:
                params[key] = raw
        try:
            run = create_run(
                nb, params, area=area, event=event, trigger=TriggerKind.CLI, enqueue=opts["use_queue"]
            )
        except (RunRequestError, ParameterError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f"created run {run.id}")
        if not opts["use_queue"]:
            run = run_pipeline(str(run.id))
            style = self.style.SUCCESS if run.status == "published" else self.style.ERROR
            self.stdout.write(style(f"{run.status}: {run.output_url or run.error}"))
