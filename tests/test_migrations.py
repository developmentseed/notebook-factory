"""The Event → StacItem data migration keeps existing Montandon rows and runs linked."""

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

BEFORE = [("events", "0002_initial"), ("notebooks", "0001_initial")]
AFTER = [("events", "0005_require_catalog"), ("notebooks", "0002_stac_item")]


def _migrate(targets):
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(targets)
    return executor.loader.project_state(targets).apps


@pytest.mark.django_db(transaction=True)
def test_events_become_stac_items_in_a_montandon_catalog(monkeypatch):
    monkeypatch.setenv("MONTANDON_STAC_URL", "https://monty.example.org/stac")
    monkeypatch.setenv("MONTANDON_API_TOKEN", "tok")
    try:
        old = _migrate(BEFORE)
        Event = old.get_model("events", "Event")
        Rule = old.get_model("events", "EventTriggerRule")
        Link = old.get_model("events", "EventRunLink")
        Poller = old.get_model("events", "PollerState")
        Notebook = old.get_model("notebooks", "AnalysisNotebook")
        Run = old.get_model("notebooks", "NotebookRun")

        nb = Notebook.objects.create(
            slug="impact", title="Impact", notebook_path="x.ipynb", requires_event=True
        )
        ev = Event.objects.create(
            stac_id="ev-1", collection="reference-events", monty_corr_id="corr-1", country_codes=["NPL"]
        )
        rule = Rule.objects.create(name="r", notebook=nb, collections=["reference-events"])
        Link.objects.create(event=ev, rule=rule)
        Poller.objects.create(collection="reference-events", items_seen=3)
        run = Run.objects.create(notebook=nb, event=ev, trigger_rule=rule)

        new = _migrate(AFTER)
        Catalog = new.get_model("events", "StacCatalog")
        Item = new.get_model("events", "StacItem")
        catalog = Catalog.objects.get(slug="montandon")
        assert (catalog.url, catalog.api_key, catalog.profile) == (
            "https://monty.example.org/stac",
            "tok",
            "montandon",
        )
        item = Item.objects.get()
        assert (item.catalog_id, item.item_id, item.correlation_id) == (catalog.pk, "ev-1", "corr-1")
        assert new.get_model("events", "TriggerRule").objects.get().catalog_id == catalog.pk
        assert new.get_model("events", "ItemRuleLink").objects.get().item_id == item.pk
        assert new.get_model("events", "PollerState").objects.get().catalog_id == catalog.pk
        migrated = new.get_model("notebooks", "NotebookRun").objects.get(pk=run.pk)
        assert migrated.stac_item_id == item.pk and migrated.trigger_rule_id == rule.pk
        assert new.get_model("notebooks", "AnalysisNotebook").objects.get().requires_stac_item is True
    finally:
        # Leave the schema at the latest state for the other tests.
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())


@pytest.mark.django_db(transaction=True)
def test_fresh_database_gets_no_catalog():
    _migrate(BEFORE)
    apps = _migrate(AFTER)
    assert not apps.get_model("events", "StacCatalog").objects.exists()
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(executor.loader.graph.leaf_nodes())


BEFORE_FACETS = [
    ("catalog", "0001_initial"),
    ("events", "0005_require_catalog"),
    ("notebooks", "0002_stac_item"),
]


@pytest.mark.django_db(transaction=True)
def test_hazards_and_use_cases_become_facet_options():
    try:
        old = _migrate(BEFORE_FACETS)
        Hazard = old.get_model("catalog", "Hazard")
        Notebook = old.get_model("notebooks", "AnalysisNotebook")
        Run = old.get_model("notebooks", "NotebookRun")
        Catalog = old.get_model("events", "StacCatalog")
        Item = old.get_model("events", "StacItem")
        Rule = old.get_model("events", "TriggerRule")

        flood = Hazard.objects.create(
            key="flood", label="Flood", icon="🌊", color="#2E6FB0", monty_codes=["nat-hyd-flo"]
        )
        quake = Hazard.objects.create(key="earthquake", label="Earthquake", monty_codes=["nat-geo-ear"])
        nb = Notebook.objects.create(slug="exp", title="Exp", notebook_path="x.ipynb", use_case="uc1")
        nb.hazards.set([flood, quake])
        from_param = Run.objects.create(notebook=nb, hazard=flood, parameters={"hazard": "flood"})
        from_template = Run.objects.create(notebook=nb, hazard=quake)
        catalog = Catalog.objects.create(name="M", slug="m", url="https://m.example.org", profile="montandon")
        item = Item.objects.create(catalog=catalog, collection="c", item_id="i", hazard=quake)
        rule = Rule.objects.create(name="r", catalog=catalog, notebook=nb, collections=["c"])
        rule.hazards.set([flood])

        new = _migrate(executor_leaves())
        Facet = new.get_model("catalog", "Facet")
        Option = new.get_model("catalog", "FacetOption")
        RunFacet = new.get_model("notebooks", "RunFacet")
        hazard = Facet.objects.get(key="hazard")
        assert hazard.stac_property == "monty:hazard_codes"
        flood_opt = Option.objects.get(facet=hazard, key="flood")
        assert (flood_opt.label, flood_opt.icon, flood_opt.match_codes) == ("Flood", "🌊", ["nat-hyd-flo"])
        assert set(Option.objects.filter(facet__key="use_case").values_list("key", flat=True)) == {
            "uc1",
            "uc2",
            "uc3",
            "other",
        }
        nb_new = new.get_model("notebooks", "AnalysisNotebook").objects.get(pk=nb.pk)
        assert set(nb_new.facet_options.values_list("key", flat=True)) == {"flood", "earthquake", "uc1"}

        def tags(run):
            return {
                (rf.option.key, rf.source)
                for rf in RunFacet.objects.filter(run_id=run.pk).select_related("option")
            }

        assert tags(from_param) == {("flood", "parameter"), ("uc1", "template")}
        assert tags(from_template) == {("earthquake", "template"), ("uc1", "template")}
        item_new = new.get_model("events", "StacItem").objects.get(pk=item.pk)
        assert list(item_new.facet_options.values_list("key", flat=True)) == ["earthquake"]
        rule_new = new.get_model("events", "TriggerRule").objects.get(pk=rule.pk)
        assert list(rule_new.facet_options.values_list("key", flat=True)) == ["flood"]
    finally:
        _migrate(executor_leaves())


def executor_leaves():
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    return executor.loader.graph.leaf_nodes()
