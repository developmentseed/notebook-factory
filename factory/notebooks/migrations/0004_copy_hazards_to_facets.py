"""Copy the hard-coded Hazard model and use_case choices into generic facets and their options."""

from django.db import migrations

USE_CASES = [
    ("uc1", "Risk exposure", "#2E6FB0", 10),
    ("uc2", "Impact estimation", "#B9770E", 20),
    ("uc3", "Response prioritisation", "#C0392B", 30),
    ("other", "Other", "#6B7280", 90),
]


def forwards(apps, schema_editor):
    Facet = apps.get_model("catalog", "Facet")
    FacetOption = apps.get_model("catalog", "FacetOption")
    Hazard = apps.get_model("catalog", "Hazard")
    AnalysisNotebook = apps.get_model("notebooks", "AnalysisNotebook")
    NotebookRun = apps.get_model("notebooks", "NotebookRun")
    RunFacet = apps.get_model("notebooks", "RunFacet")
    StacItem = apps.get_model("events", "StacItem")
    TriggerRule = apps.get_model("events", "TriggerRule")

    hazard_facet, _ = Facet.objects.get_or_create(
        key="hazard",
        defaults={"label": "Hazard", "stac_property": "monty:hazard_codes", "order": 10},
    )
    use_case_facet, _ = Facet.objects.get_or_create(
        key="use_case", defaults={"label": "Use case", "order": 20}
    )

    hazard_options = {}
    for h in Hazard.objects.all():
        hazard_options[h.pk], _ = FacetOption.objects.update_or_create(
            facet=hazard_facet,
            key=h.key,
            defaults={
                "label": h.label,
                "icon": h.icon,
                "color": h.color,
                "match_codes": h.monty_codes or [],
                "order": h.order,
            },
        )
    use_case_options = {}
    for key, label, color, order in USE_CASES:
        use_case_options[key], _ = FacetOption.objects.get_or_create(
            facet=use_case_facet, key=key, defaults={"label": label, "color": color, "order": order}
        )

    for nb in AnalysisNotebook.objects.all():
        options = [hazard_options[h.pk] for h in nb.hazards.all()]
        if nb.use_case in use_case_options:
            options.append(use_case_options[nb.use_case])
        nb.facet_options.add(*options)

    runs = NotebookRun.objects.select_related("hazard", "notebook")
    for run in runs:
        rows = []
        if run.hazard_id:
            from_param = (run.parameters or {}).get("hazard") == run.hazard.key
            rows.append(
                RunFacet(
                    run=run,
                    option=hazard_options[run.hazard_id],
                    source="parameter" if from_param else "template",
                )
            )
        if run.notebook.use_case in use_case_options:
            rows.append(RunFacet(run=run, option=use_case_options[run.notebook.use_case], source="template"))
        RunFacet.objects.bulk_create(rows, ignore_conflicts=True)

    for item in StacItem.objects.exclude(hazard__isnull=True):
        item.facet_options.add(hazard_options[item.hazard_id])
    for rule in TriggerRule.objects.all():
        rule.facet_options.add(*[hazard_options[h.pk] for h in rule.hazards.all()])


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0002_facets"),
        ("events", "0006_facet_tags"),
        ("notebooks", "0003_facet_tags"),
    ]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
