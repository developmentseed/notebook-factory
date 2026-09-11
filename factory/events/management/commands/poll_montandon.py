import json

from django.core.management.base import BaseCommand

from factory.events.poller import poll


class Command(BaseCommand):
    help = "Poll Montandon once for new events and fire matching trigger rules."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true", help="Only count items; do not store or trigger"
        )

    def handle(self, *args, **opts):
        summary = poll(dry_run=opts["dry_run"])
        self.stdout.write(json.dumps(summary, indent=2, default=str))
