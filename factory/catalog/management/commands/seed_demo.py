"""
Seed reference data for a first look: the hazard and use case filters, a demo country (from GADM,
downloaded on demand), the local templates, the Montandon STAC catalog and example trigger rules.
"""

from django.core.management import call_command
from django.core.management.base import BaseCommand

from factory.catalog.models import AdminArea, Facet, FacetOption

HAZARDS = [
    ("flood", "Flood", "🌊", "#2E6FB0", ["nat-hyd-flo"], 10),
    ("cyclone", "Tropical cyclone", "🌀", "#178C8C", ["nat-met-sto-tro", "nat-met-sto"], 20),
    ("earthquake", "Earthquake", "🏚️", "#C98A16", ["nat-geo-ear"], 30),
    ("wildfire", "Wildfire", "🔥", "#C0392B", ["nat-cli-wil"], 40),
    ("heatwave", "Heatwave", "🌡️", "#B07D2B", ["nat-met-ext-hea"], 50),
    ("drought", "Drought", "🏜️", "#B07D2B", ["nat-cli-dro"], 60),
    ("landslide", "Landslide", "⛰️", "#6B7280", ["nat-geo-mmd-lan", "nat-hyd-mmw-lan"], 70),
    ("tsunami", "Tsunami", "🌊", "#2E6FB0", ["nat-geo-ear-tsu"], 80),
    ("volcano", "Volcanic activity", "🌋", "#C0392B", ["nat-geo-vol"], 90),
]

USE_CASES = [
    ("uc1", "Risk exposure", "#2E6FB0", 10),
    ("uc2", "Impact estimation", "#B9770E", 20),
    ("uc3", "Response prioritisation", "#C0392B", 30),
    ("other", "Other", "#6B7280", 90),
]


class Command(BaseCommand):
    help = (
        "Seed the hazard / use case filters, demo boundaries (GADM), templates, "
        "the Montandon STAC catalog and example trigger rules."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--countries", default="NPL", help="Comma separated ISO3 to download from GADM (empty to skip)"
        )
        parser.add_argument("--levels", default="0,1,2")
        parser.add_argument("--skip-boundaries", action="store_true")

    def handle(self, *args, **opts):
        hazard, _ = Facet.objects.get_or_create(
            key="hazard", defaults={"label": "Hazard", "stac_property": "monty:hazard_codes", "order": 10}
        )
        for key, label, icon, color, codes, order in HAZARDS:
            FacetOption.objects.update_or_create(
                facet=hazard,
                key=key,
                defaults={"label": label, "icon": icon, "color": color, "match_codes": codes, "order": order},
            )
        use_case, _ = Facet.objects.get_or_create(key="use_case", defaults={"label": "Use case", "order": 20})
        for key, label, color, order in USE_CASES:
            FacetOption.objects.update_or_create(
                facet=use_case, key=key, defaults={"label": label, "color": color, "order": order}
            )
        self.stdout.write(self.style.SUCCESS(f"filters: {len(HAZARDS)} hazards, {len(USE_CASES)} use cases"))

        if not opts["skip_boundaries"] and opts["countries"]:
            for iso3 in [c.strip() for c in opts["countries"].split(",") if c.strip()]:
                if AdminArea.objects.filter(country_id=iso3.upper(), level=1).exists():
                    self.stdout.write(f"{iso3}: boundaries already loaded")
                    continue
                call_command("import_gadm", iso3, levels=opts["levels"])

        call_command("sync_templates")
        self._seed_rules()

    def _seed_rules(self):
        from django.conf import settings

        from factory.events.models import CatalogProfile, StacCatalog, TriggerRule
        from factory.notebooks.models import AnalysisNotebook

        cfg = settings.MONTANDON_SEED
        montandon, created = StacCatalog.objects.get_or_create(
            slug="montandon",
            defaults={
                "name": "Montandon",
                "url": cfg["URL"],
                "api_key": cfg["API_KEY"],
                "profile": CatalogProfile.MONTANDON,
                "writeback_enabled": cfg["WRITEBACK_ENABLED"],
            },
        )
        if created:
            self.stdout.write(f"created STAC catalog: Montandon ({cfg['URL']})")

        uc2 = AnalysisNotebook.objects.filter(slug="uc2-impact-estimation").first()
        uc3 = AnalysisNotebook.objects.filter(slug="uc3-response-prioritisation").first()
        if uc2:
            rule, created = TriggerRule.objects.get_or_create(
                name="New event → impact estimation",
                defaults={
                    "enabled": False,
                    "catalog": montandon,
                    "notebook": uc2,
                    "collections": ["reference-events"],
                    "roles": ["event"],
                    "parameter_template": {"population_source": "worldpop_100m"},
                    "area_level": None,
                    "notify_emails": [],
                },
            )
            if created:
                rule.facet_options.set(
                    FacetOption.objects.filter(
                        facet__key="hazard", key__in=["flood", "cyclone", "earthquake", "wildfire"]
                    )
                )
                self.stdout.write("created example trigger rule (disabled): New event → impact estimation")
        if uc3:
            rule, created = TriggerRule.objects.get_or_create(
                name="New event → response prioritisation",
                defaults={
                    "enabled": False,
                    "catalog": montandon,
                    "notebook": uc3,
                    "collections": ["reference-events"],
                    "roles": ["event"],
                    "parameter_template": {"weighting": "geometric"},
                },
            )
            if created:
                self.stdout.write(
                    "created example trigger rule (disabled): New event → response prioritisation"
                )
