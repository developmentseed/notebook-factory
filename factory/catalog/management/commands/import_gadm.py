from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from factory.catalog.importers import download_gadm, gadm_mapping, import_datasource


class Command(BaseCommand):
    help = "Download GADM 4.1 boundaries for a country and import the requested levels (0,1,2 by default)."

    def add_arguments(self, parser):
        parser.add_argument("iso3", nargs="+", help="ISO3 country codes, e.g. NPL VNM")
        parser.add_argument("--levels", default="0,1,2", help="Comma separated admin levels")
        parser.add_argument("--cache-dir", default=str(Path(settings.BASE_DIR) / "data" / "gadm"))

    def handle(self, *args, **opts):
        levels = [int(x) for x in opts["levels"].split(",") if x.strip() != ""]
        cache = Path(opts["cache_dir"])
        for iso3 in opts["iso3"]:
            iso3 = iso3.upper()
            for level in sorted(levels):
                try:
                    path = download_gadm(iso3, level, cache)
                except Exception as exc:  # noqa: BLE001
                    self.stderr.write(self.style.WARNING(f"{iso3} level {level}: {exc}"))
                    continue
                created, updated = import_datasource(
                    path, level=level, mapping=gadm_mapping(level), country_iso3=iso3
                )
                self.stdout.write(
                    self.style.SUCCESS(f"{iso3} level {level}: {created} created, {updated} updated")
                )
