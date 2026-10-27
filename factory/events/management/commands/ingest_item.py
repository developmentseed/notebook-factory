import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from factory.events.matching import upsert_item
from factory.events.models import StacCatalog
from factory.events.poller import fire_rules


class Command(BaseCommand):
    help = "Ingest a single STAC item (from a file or from a catalog) and fire matching rules."

    def add_arguments(self, parser):
        parser.add_argument("--catalog", required=True, help="Catalog slug")
        parser.add_argument("--file", help="Path to a STAC item JSON")
        parser.add_argument("--collection", help="Collection id (with --item, or to override the file's)")
        parser.add_argument("--item", help="Item id (with --collection)")
        parser.add_argument("--no-fire", action="store_true", help="Store the item without triggering runs")

    def handle(self, *args, **opts):
        catalog = StacCatalog.objects.filter(slug=opts["catalog"]).first()
        if catalog is None:
            raise CommandError(f"Unknown catalog {opts['catalog']}")
        if opts["file"]:
            raw = json.loads(Path(opts["file"]).read_text())
        elif opts["collection"] and opts["item"]:
            with catalog.client() as client:
                raw = client.get_item(opts["collection"], opts["item"])
        else:
            raise CommandError("Pass --file or --collection and --item")
        item, created, _ = upsert_item(catalog, raw, collection=opts["collection"])
        self.stdout.write(f"{'created' if created else 'updated'} STAC item {item.pk}: {item}")
        if not opts["no_fire"]:
            n = fire_rules(item)
            self.stdout.write(self.style.SUCCESS(f"{n} run(s) created"))
