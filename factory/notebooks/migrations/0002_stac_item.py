import django.db.models.deletion
from django.db import migrations, models

TRIGGER_CHOICES = [
    ("manual", "Manual (UI)"),
    ("api", "API"),
    ("auto", "Automatic (trigger rule)"),
    ("cli", "Command line"),
]


class Migration(migrations.Migration):
    dependencies = [
        ("events", "0003_stac_catalogs"),
        ("notebooks", "0001_initial"),
    ]

    operations = [
        migrations.RenameField(model_name="notebookrun", old_name="event", new_name="stac_item"),
        migrations.RenameField(
            model_name="analysisnotebook", old_name="requires_event", new_name="requires_stac_item"
        ),
        migrations.AlterField(
            model_name="analysisnotebook",
            name="requires_stac_item",
            field=models.BooleanField(default=False, help_text="Run needs a STAC item"),
        ),
        migrations.AddField(
            model_name="analysisnotebook",
            name="stac_hints",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text="Soft picker hints: {profile, collections, roles}. Never enforced.",
            ),
        ),
        migrations.AlterField(
            model_name="analysisnotebook",
            name="supports_writeback",
            field=models.BooleanField(
                default=False,
                help_text="Notebook produces a STAC collection to write back to the item's catalog",
            ),
        ),
        migrations.AlterField(
            model_name="notebookrun",
            name="stac_item",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="runs",
                to="events.stacitem",
            ),
        ),
        migrations.AlterField(
            model_name="notebookrun",
            name="trigger",
            field=models.CharField(choices=TRIGGER_CHOICES, default="manual", max_length=10),
        ),
        migrations.AlterField(
            model_name="runbatch",
            name="trigger",
            field=models.CharField(choices=TRIGGER_CHOICES, default="manual", max_length=10),
        ),
        migrations.AlterField(
            model_name="notebookrun",
            name="writeback_ref",
            field=models.CharField(
                blank=True, help_text="Collection id written to the item's catalog", max_length=400
            ),
        ),
    ]
