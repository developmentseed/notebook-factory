import json

from django.core.management.base import BaseCommand, CommandError

from factory.catalog.models import AdminArea
from factory.events.matching import fetch_item
from factory.events.models import StacCatalog, StacItem
from factory.events.stac_client import StacClientError
from factory.notebooks.models import AnalysisNotebook, TriggerKind
from factory.notebooks.parameters import ParameterError
from factory.notebooks.pipeline.runner import run_pipeline
from factory.notebooks.services import RunRequestError, create_run


class Command(BaseCommand):
    help = "Run a template synchronously from the command line (no worker needed)."

    def add_arguments(self, parser):
        parser.add_argument("slug")
        parser.add_argument("--area", help="Admin area code (e.g. GADM GID) or numeric id")
        parser.add_argument("--stac-item", help="Stored StacItem pk, STAC item id or correlation id")
        parser.add_argument(
            "--catalog", help="Catalog slug: fetch the item live (with --collection and --item)"
        )
        parser.add_argument("--collection", help="Collection id (with --catalog)")
        parser.add_argument("--item", help="Item id (with --catalog)")
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
        stac_item = self._stac_item(opts)
        params = {}
        for kv in opts["param"]:
            key, _, raw = kv.partition("=")
            try:
                params[key] = json.loads(raw)
            except json.JSONDecodeError:
                params[key] = raw
        try:
            run = create_run(
                nb, params, area=area, stac_item=stac_item, trigger=TriggerKind.CLI, enqueue=opts["use_queue"]
            )
        except (RunRequestError, ParameterError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f"created run {run.id}")
        if not opts["use_queue"]:
            run = run_pipeline(str(run.id))
            style = self.style.SUCCESS if run.status == "published" else self.style.ERROR
            self.stdout.write(style(f"{run.status}: {run.output_url or run.error}"))

    def _stac_item(self, opts) -> StacItem | None:
        if opts["catalog"]:
            catalog = StacCatalog.objects.filter(slug=opts["catalog"]).first()
            if catalog is None:
                raise CommandError(f"Unknown catalog {opts['catalog']}")
            if not (opts["collection"] and opts["item"]):
                raise CommandError("--catalog needs --collection and --item")
            try:
                return fetch_item(catalog, opts["collection"], opts["item"])
            except StacClientError as exc:
                raise CommandError(str(exc)) from exc
        ref = opts["stac_item"]
        if not ref:
            return None
        item = (
            StacItem.objects.filter(item_id=ref).first()
            or StacItem.objects.filter(correlation_id=ref).first()
        )
        if item is None and ref.isdigit():
            item = StacItem.objects.filter(pk=int(ref)).first()
        if item is None:
            raise CommandError(f"Unknown STAC item {ref}")
        return item
