"""
Seed reference data for a first look: hazards, a demo country (from GADM,
downloaded on demand), the local templates and one example trigger rule.
"""

from django.core.management import call_command
from django.core.management.base import BaseCommand

from factory.catalog.models import AdminArea, Hazard

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


class Command(BaseCommand):
    help = "Seed hazards, demo boundaries (GADM), templates and an example trigger rule."

    def add_arguments(self, parser):
        parser.add_argument(
            "--countries", default="NPL", help="Comma separated ISO3 to download from GADM (empty to skip)"
        )
        parser.add_argument("--levels", default="0,1,2")
        parser.add_argument("--skip-boundaries", action="store_true")

    def handle(self, *args, **opts):
        for key, label, icon, color, codes, order in HAZARDS:
            Hazard.objects.update_or_create(
                key=key,
                defaults={"label": label, "icon": icon, "color": color, "monty_codes": codes, "order": order},
            )
        self.stdout.write(self.style.SUCCESS(f"{len(HAZARDS)} hazards"))

        if not opts["skip_boundaries"] and opts["countries"]:
            for iso3 in [c.strip() for c in opts["countries"].split(",") if c.strip()]:
                if AdminArea.objects.filter(country_id=iso3.upper(), level=1).exists():
                    self.stdout.write(f"{iso3}: boundaries already loaded")
                    continue
                call_command("import_gadm", iso3, levels=opts["levels"])

        call_command("sync_templates")
        self._seed_rules()

    def _seed_rules(self):
        from factory.events.models import EventTriggerRule
        from factory.notebooks.models import AnalysisNotebook

        uc2 = AnalysisNotebook.objects.filter(slug="uc2-impact-estimation").first()
        uc3 = AnalysisNotebook.objects.filter(slug="uc3-response-prioritisation").first()
        if uc2:
            rule, created = EventTriggerRule.objects.get_or_create(
                name="New event → impact estimation",
                defaults={
                    "enabled": False,
                    "notebook": uc2,
                    "collections": ["reference-events"],
                    "roles": ["event"],
                    "parameter_template": {"population_source": "worldpop_100m"},
                    "area_level": None,
                    "notify_emails": [],
                },
            )
            if created:
                rule.hazards.set(
                    Hazard.objects.filter(key__in=["flood", "cyclone", "earthquake", "wildfire"])
                )
                self.stdout.write("created example trigger rule (disabled): New event → impact estimation")
        if uc3:
            rule, created = EventTriggerRule.objects.get_or_create(
                name="New event → response prioritisation",
                defaults={
                    "enabled": False,
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
