import copy

from factory.events.matching import render_parameters, rule_matches, upsert_event
from factory.events.models import EventRunLink, EventTriggerRule
from factory.events.poller import fire_rules, poll
from factory.notebooks.models import NotebookRun, TriggerKind


def test_upsert_event_maps_hazard_and_countries(hazards, sample_item):
    event, created, changed = upsert_event(sample_item)
    assert created and changed
    assert event.hazard == hazards["earthquake"]
    assert event.country_codes == ["NPL"] and event.monty_corr_id == "20260901-NPL-GEO-EAR-GRO-1-GCDB"
    assert event.geom is not None and event.occurred_at.year == 2026
    again, created2, changed2 = upsert_event(sample_item)
    assert again.pk == event.pk and not created2 and not changed2


def test_rule_matching(hazards, event_template, sample_item):
    event, *_ = upsert_event(sample_item)
    rule = EventTriggerRule.objects.create(
        name="r", notebook=event_template, collections=["reference-events"], roles=["event"]
    )
    assert rule_matches(rule, event)
    rule.hazards.set([hazards["flood"]])
    assert not rule_matches(rule, event)
    rule.hazards.set([hazards["earthquake"]])
    rule.country_codes = ["VNM"]
    assert not rule_matches(rule, event)
    rule.country_codes = ["npl"]
    assert rule_matches(rule, event)
    rule.collections = ["usgs-events"]
    assert not rule_matches(rule, event)


def test_render_parameters_with_jinja(hazards, sample_item):
    event, *_ = upsert_event(sample_item)
    out = render_parameters(
        {
            "corr": "{{ event.monty_corr_id }}",
            "n": 3,
            "code": "{{ props['monty:hazard_codes'][0] }}",
            "h": "{{ hazard }}",
        },
        event,
    )
    assert out == {
        "corr": "20260901-NPL-GEO-EAR-GRO-1-GCDB",
        "n": 3,
        "code": "nat-geo-ear-gro",
        "h": "earthquake",
    }


def test_fire_rules_country_level_and_dedupe(hazards, nepal, event_template, sample_item):
    event, *_ = upsert_event(sample_item)
    rule = EventTriggerRule.objects.create(
        name="r", notebook=event_template, collections=["reference-events"], parameter_template={"radius": 25}
    )
    assert fire_rules(event) == 1
    run = NotebookRun.objects.get()
    assert (
        run.area == nepal["root"]
        and run.event == event
        and run.trigger == TriggerKind.AUTO
        and run.parameters["radius"] == 25
    )
    assert EventRunLink.objects.filter(event=event, rule=rule).exists()
    assert fire_rules(event) == 0  # already fired


def test_fire_rules_fan_out_over_level(hazards, nepal, event_template, sample_item):
    event, *_ = upsert_event(sample_item)
    EventTriggerRule.objects.create(
        name="r", notebook=event_template, collections=["reference-events"], area_level=2
    )
    assert fire_rules(event) == 4
    assert NotebookRun.objects.filter(batch__isnull=False).count() == 4


def test_rerun_on_update(hazards, nepal, event_template, sample_item):
    event, *_ = upsert_event(sample_item)
    EventTriggerRule.objects.create(
        name="r", notebook=event_template, collections=["reference-events"], rerun_on_update=True
    )
    assert fire_rules(event) == 1
    updated = copy.deepcopy(sample_item)
    updated["properties"]["updated"] = "2026-09-02T00:00:00Z"
    event, created, changed = upsert_event(updated)
    assert changed and not created
    assert fire_rules(event, created=False, changed=True) == 1
    assert NotebookRun.objects.count() == 2


class FakeClient:
    def __init__(self, items):
        self.items = items
        self.calls = []

    def search(self, collections, **kwargs):
        self.calls.append((collections, kwargs))
        return iter([i for i in self.items if i["collection"] in collections])


def test_poll_uses_state_and_creates_runs(hazards, nepal, event_template, sample_item):
    EventTriggerRule.objects.create(name="r", notebook=event_template, collections=["reference-events"])
    client = FakeClient([sample_item])
    summary = poll(client=client)
    assert (
        summary["events_new"] == 1
        and summary["runs_created"] == 1
        and summary["collections"] == {"reference-events": 1}
    )
    from factory.events.models import PollerState

    state = PollerState.objects.get(collection="reference-events")
    assert state.last_item_datetime == NotebookRun.objects.get().event.occurred_at
    summary2 = poll(client=client)
    assert summary2["events_new"] == 0 and summary2["runs_created"] == 0
    # second poll starts from the last seen datetime (minus overlap)
    assert client.calls[1][1]["since"] < state.last_item_datetime
