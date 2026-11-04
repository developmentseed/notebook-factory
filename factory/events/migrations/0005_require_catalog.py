import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("events", "0004_montandon_catalog")]

    operations = [
        migrations.AlterField(
            model_name="stacitem",
            name="catalog",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT, related_name="items", to="events.staccatalog"
            ),
        ),
        migrations.AlterField(
            model_name="triggerrule",
            name="catalog",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="trigger_rules",
                to="events.staccatalog",
            ),
        ),
        migrations.AlterField(
            model_name="pollerstate",
            name="catalog",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="poller_states",
                to="events.staccatalog",
            ),
        ),
        migrations.AddConstraint(
            model_name="stacitem",
            constraint=models.UniqueConstraint(
                fields=("catalog", "collection", "item_id"), name="uniq_stacitem_catalog_collection_item"
            ),
        ),
        migrations.AddConstraint(
            model_name="pollerstate",
            constraint=models.UniqueConstraint(
                fields=("catalog", "collection"), name="uniq_pollerstate_catalog_collection"
            ),
        ),
        migrations.AddConstraint(
            model_name="itemrulelink",
            constraint=models.UniqueConstraint(fields=("item", "rule"), name="uniq_item_rule"),
        ),
    ]
