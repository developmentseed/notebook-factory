from pathlib import Path

import httpx
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from factory.catalog.importers import download_gadm, gadm_mapping, import_datasource, list_gadm_countries
from factory.catalog.models import AdminArea, AdminAreaSource


def _codes(value: str) -> set[str]:
    return {c.strip().upper() for c in value.split(",") if c.strip()}


class Command(BaseCommand):
    help = "Download GADM 4.1 boundaries for some or all countries and import the requested levels."

    def add_arguments(self, parser):
        parser.add_argument("iso3", nargs="*", help="ISO3 country codes, e.g. NPL VNM")
        parser.add_argument("--all", action="store_true", help="Every country listed in the GADM 4.1 index")
        parser.add_argument("--levels", default="0,1,2", help="Comma separated admin levels")
        parser.add_argument("--exclude", default="", help="Comma separated ISO3 codes to leave out")
        parser.add_argument(
            "--skip-existing", action="store_true", help="Skip a country/level that already has GADM areas"
        )
        parser.add_argument("--cache-dir", default=str(Path(settings.BASE_DIR) / "data" / "gadm"))

    def handle(self, *args, **opts):
        if bool(opts["iso3"]) == opts["all"]:
            raise CommandError("Pass either ISO3 codes or --all.")
        levels = sorted({int(x) for x in opts["levels"].split(",") if x.strip() != ""})
        cache = Path(opts["cache_dir"])
        exclude = _codes(opts["exclude"])

        if opts["all"]:
            try:
                available = list_gadm_countries()
            except Exception as exc:  # noqa: BLE001
                raise CommandError(f"Could not read the GADM index: {exc}") from exc
            # Only the levels GADM publishes for each country, so nothing 404s.
            plan = {iso3: [lv for lv in levels if lv in published] for iso3, published in available.items()}
        else:
            plan = {iso3.upper(): levels for iso3 in opts["iso3"]}

        failed: list[tuple[str, int, str]] = []
        imported = skipped = 0
        for iso3 in sorted(plan):
            if iso3 in exclude:
                continue
            for level in plan[iso3]:
                if opts["skip_existing"] and self._exists(iso3, level):
                    skipped += 1
                    if opts["verbosity"] > 1:
                        self.stdout.write(f"{iso3} level {level}: already imported, skipped")
                    continue
                try:
                    path = download_gadm(iso3, level, cache)
                    created, updated = import_datasource(
                        path, level=level, mapping=gadm_mapping(level), country_iso3=iso3
                    )
                except Exception as exc:  # noqa: BLE001
                    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 404:
                        self.stderr.write(self.style.WARNING(f"{iso3} level {level}: not published by GADM"))
                    else:
                        failed.append((iso3, level, str(exc)))
                        self.stderr.write(self.style.WARNING(f"{iso3} level {level}: {exc}"))
                    # Deeper levels need this one for their parents, so they wait for a re-run.
                    break
                imported += 1
                self.stdout.write(
                    self.style.SUCCESS(f"{iso3} level {level}: {created} created, {updated} updated")
                )

        self.stdout.write(f"{imported} imported, {skipped} skipped, {len(failed)} failed")
        if failed:
            self.stderr.write("Failed:")
            for iso3, level, error in failed:
                self.stderr.write(f"  {iso3} {level}: {error}")
            raise CommandError(f"{len(failed)} country/level imports failed")

    @staticmethod
    def _exists(iso3: str, level: int) -> bool:
        return AdminArea.objects.filter(source=AdminAreaSource.GADM, country_id=iso3, level=level).exists()
