"""Attach existing Montandon events, rules and poller state to a seeded Montandon catalog."""

import environ
from django.db import migrations

STAGING_URL = "https://montandon-eoapi-stage.ifrc.org/stac"


def forwards(apps, schema_editor):
    StacCatalog = apps.get_model("events", "StacCatalog")
    StacItem = apps.get_model("events", "StacItem")
    TriggerRule = apps.get_model("events", "TriggerRule")
    PollerState = apps.get_model("events", "PollerState")
    if not (StacItem.objects.exists() or TriggerRule.objects.exists() or PollerState.objects.exists()):
        return
    env = environ.Env()
    catalog, _ = StacCatalog.objects.get_or_create(
        slug="montandon",
        defaults={
            "name": "Montandon",
            "url": env("MONTANDON_STAC_URL", default=STAGING_URL),
            "api_key": env("MONTANDON_API_TOKEN", default=""),
            "profile": "montandon",
            "writeback_enabled": env.bool("MONTANDON_WRITEBACK_ENABLED", default=False),
        },
    )
    for model in (StacItem, TriggerRule, PollerState):
        model.objects.filter(catalog__isnull=True).update(catalog=catalog)


class Migration(migrations.Migration):
    dependencies = [("events", "0003_stac_catalogs")]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
