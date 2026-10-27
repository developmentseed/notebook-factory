"""Generalise Montandon events into items from any STAC catalog (schema part 1)."""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0001_initial"),
        ("events", "0002_initial"),
        ("notebooks", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="StacCatalog",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("name", models.CharField(max_length=120)),
                ("slug", models.SlugField(max_length=80, unique=True)),
                (
                    "url",
                    models.URLField(
                        help_text="STAC API root, e.g. https://earth-search.aws.element84.com/v1",
                        max_length=400,
                    ),
                ),
                (
                    "api_key",
                    models.CharField(
                        blank=True, help_text="Sent as 'Authorization: Bearer <key>' when set", max_length=500
                    ),
                ),
                (
                    "profile",
                    models.CharField(
                        choices=[("generic", "Generic STAC"), ("montandon", "Montandon (monty:* extension)")],
                        default="generic",
                        help_text="How extra metadata (hazard, countries, correlation id) is read from items",
                        max_length=20,
                    ),
                ),
                (
                    "writeback_enabled",
                    models.BooleanField(
                        default=False,
                        help_text="Notebook-produced STAC collections may be written to this catalog",
                    ),
                ),
                ("is_active", models.BooleanField(default=True)),
                ("last_checked_at", models.DateTimeField(blank=True, null=True)),
                ("last_error", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["name"]},
        ),
        # Old constraints reference the old field names; drop them before renaming.
        migrations.RemoveConstraint(model_name="event", name="uniq_event_collection_item"),
        migrations.RemoveConstraint(model_name="eventrunlink", name="uniq_event_rule"),
        migrations.RenameModel(old_name="Event", new_name="StacItem"),
        migrations.RenameModel(old_name="EventTriggerRule", new_name="TriggerRule"),
        migrations.RenameModel(old_name="EventRunLink", new_name="ItemRuleLink"),
        migrations.RenameField(model_name="stacitem", old_name="stac_id", new_name="item_id"),
        migrations.RenameField(model_name="stacitem", old_name="monty_corr_id", new_name="correlation_id"),
        migrations.RenameField(model_name="itemrulelink", old_name="event", new_name="item"),
        migrations.AlterField(
            model_name="stacitem",
            name="correlation_id",
            field=models.CharField(
                blank=True, db_index=True, help_text="monty:corr_id (Montandon profile)", max_length=200
            ),
        ),
        migrations.AlterField(
            model_name="stacitem",
            name="hazard",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="stac_items",
                to="catalog.hazard",
            ),
        ),
        migrations.AlterField(
            model_name="itemrulelink",
            name="item",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE, related_name="rule_links", to="events.stacitem"
            ),
        ),
        migrations.AlterField(
            model_name="itemrulelink",
            name="rule",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="item_links",
                to="events.triggerrule",
            ),
        ),
        migrations.AlterField(
            model_name="pollerstate",
            name="collection",
            field=models.CharField(max_length=120),
        ),
        migrations.AlterField(
            model_name="triggerrule",
            name="hazards",
            field=models.ManyToManyField(
                blank=True,
                help_text="Only these hazards (empty = any). Needs a catalog profile that extracts hazards.",
                to="catalog.hazard",
            ),
        ),
        migrations.AlterField(
            model_name="triggerrule",
            name="country_codes",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Only these ISO3 countries (empty = any). Needs a catalog profile that extracts countries.",
            ),
        ),
        migrations.AlterField(
            model_name="triggerrule",
            name="parameter_template",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text="Parameters for the run. String values may use Jinja, e.g. {{ item.correlation_id }}.",
            ),
        ),
        migrations.AlterField(
            model_name="triggerrule",
            name="area_level",
            field=models.PositiveSmallIntegerField(
                blank=True,
                help_text="Fan out over all areas at this admin level in the item's countries; null = country-level run",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="triggerrule",
            name="search_filter",
            field=models.JSONField(
                blank=True, default=dict, help_text="Optional CQL2-JSON filter added to the search body"
            ),
        ),
        # Nullable for now; 0004 fills them, 0005 makes them required.
        migrations.AddField(
            model_name="stacitem",
            name="catalog",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="items",
                to="events.staccatalog",
            ),
        ),
        migrations.AddField(
            model_name="triggerrule",
            name="catalog",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="trigger_rules",
                to="events.staccatalog",
            ),
        ),
        migrations.AddField(
            model_name="pollerstate",
            name="catalog",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="poller_states",
                to="events.staccatalog",
            ),
        ),
    ]
