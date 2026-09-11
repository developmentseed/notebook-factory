from django.core.management.base import BaseCommand

from factory.notebooks.loader import sync_templates


class Command(BaseCommand):
    help = "Load / refresh notebook templates from the local templates directory."

    def add_arguments(self, parser):
        parser.add_argument("--dir", help="Templates directory (default: settings)")
        parser.add_argument("--deactivate-missing", action="store_true")

    def handle(self, *args, **opts):
        synced = sync_templates(opts["dir"], deactivate_missing=opts["deactivate_missing"])
        for nb in synced:
            self.stdout.write(f"  {nb.slug:32s} {nb.title} ({nb.parameter_count} params)")
        self.stdout.write(self.style.SUCCESS(f"{len(synced)} templates synced"))
