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
