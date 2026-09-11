import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from factory.events.matching import upsert_event
from factory.events.montandon import MontandonClient
from factory.events.poller import fire_rules


class Command(BaseCommand):
    help = "Ingest a single STAC item (from a file or from Montandon) and fire matching rules."

    def add_arguments(self, parser):
        parser.add_argument("--file", help="Path to a STAC item JSON")
        parser.add_argument("--collection", help="Montandon collection (with --item)")
        parser.add_argument("--item", help="Montandon item id (with --collection)")
        parser.add_argument("--no-fire", action="store_true", help="Store the event without triggering runs")

    def handle(self, *args, **opts):
        if opts["file"]:
            item = json.loads(Path(opts["file"]).read_text())
        elif opts["collection"] and opts["item"]:
            item = MontandonClient().get_item(opts["collection"], opts["item"])
        else:
            raise CommandError("Pass --file or --collection and --item")
        event, created, _ = upsert_event(item)
        self.stdout.write(f"{'created' if created else 'updated'} event {event.pk}: {event}")
        if not opts["no_fire"]:
            n = fire_rules(event)
            self.stdout.write(self.style.SUCCESS(f"{n} run(s) created"))
