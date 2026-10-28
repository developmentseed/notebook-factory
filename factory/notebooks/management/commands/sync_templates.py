from django.core.management.base import BaseCommand

from factory.notebooks.loader import sync_templates


class Command(BaseCommand):
    help = (
        "Import templates from the templates directory: new folders become templates, changed folders "
        "become new versions (never over a version made in the app unless --force)."
    )

    def add_arguments(self, parser):
        parser.add_argument("--dir", help="Templates directory (default: settings)")
        parser.add_argument(
            "--deactivate-missing", action="store_true", help="Hide local templates whose folder is gone"
        )
        parser.add_argument(
            "--only-missing", action="store_true", help="Only import templates that don't exist"
        )
        parser.add_argument(
            "--convert", action="store_true", help="Turn templates still served from the folder into bundles"
        )
        parser.add_argument(
            "--force", action="store_true", help="Add a version even over one made in the app"
        )
        parser.add_argument(
            "--overwrite-metadata",
            action="store_true",
            help="Also update title, summary, description, active and filters of existing templates",
        )
        parser.add_argument("--dry-run", action="store_true", help="Report what would change")

    def handle(self, *args, **opts):
        results = sync_templates(
            opts["dir"],
            deactivate_missing=opts["deactivate_missing"],
            only_missing=opts["only_missing"],
            convert=opts["convert"],
            force=opts["force"],
            overwrite_metadata=opts["overwrite_metadata"],
            dry_run=opts["dry_run"],
        )
        for r in results:
            line = f"  {r.slug:32s} {r.action}"
            if r.notebook is not None and r.notebook.current_version_id:
                line += f" (v{r.notebook.current_version.number}, {r.notebook.parameter_count} params)"
            if r.detail:
                line += f" — {r.detail}"
            self.stdout.write(line)
        prefix = "Would sync" if opts["dry_run"] else "Synced"
        self.stdout.write(self.style.SUCCESS(f"{prefix} {len(results)} templates"))
