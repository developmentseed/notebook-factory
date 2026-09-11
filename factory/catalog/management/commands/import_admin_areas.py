from django.core.management.base import BaseCommand, CommandError

from factory.catalog.importers import FieldMapping, cod_mapping, gadm_mapping, import_datasource
from factory.catalog.models import AdminAreaSource


class Command(BaseCommand):
    help = "Import administrative boundaries from a GeoJSON / GeoPackage / Shapefile."

    def add_arguments(self, parser):
        parser.add_argument("path")
        parser.add_argument("--level", type=int, required=True)
        parser.add_argument("--source", choices=[c[0] for c in AdminAreaSource.choices], default="gadm")
        parser.add_argument("--country", help="Force ISO3 country code (otherwise read from the data)")
        parser.add_argument("--code-field", help="Custom code attribute (with --source custom)")
        parser.add_argument("--name-field", help="Custom name attribute (with --source custom)")
        parser.add_argument("--parent-field", help="Custom parent code attribute (with --source custom)")
        parser.add_argument("--layer", type=int, default=0)

    def handle(self, *args, **opts):
        level = opts["level"]
        if opts["source"] == "gadm":
            mapping = gadm_mapping(level)
        elif opts["source"] == "cod":
            mapping = cod_mapping(level)
        else:
            if not (opts["code_field"] and opts["name_field"]):
                raise CommandError("--code-field and --name-field are required with --source custom")
            mapping = FieldMapping(
                code=opts["code_field"], name=opts["name_field"], parent_code=opts["parent_field"]
            )
        created, updated = import_datasource(
            opts["path"],
            level=level,
            mapping=mapping,
            source=opts["source"],
            country_iso3=opts["country"],
            layer_index=opts["layer"],
        )
        self.stdout.write(self.style.SUCCESS(f"Imported level {level}: {created} created, {updated} updated"))
