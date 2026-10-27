import copy

import httpx
import pytest

from factory.events.matching import fetch_item, render_parameters, rule_matches, upsert_item
from factory.events.models import ItemRuleLink, PollerState, StacItem, TriggerRule
from factory.events.poller import fire_rules, poll
from factory.events.stac_client import StacClient, StacClientError
from factory.notebooks.models import NotebookRun, TriggerKind


def test_montandon_profile_maps_hazard_and_countries(hazards, montandon, sample_item):
    item, created, changed = upsert_item(montandon, sample_item)
    assert created and changed
    assert item.hazard == hazards["earthquake"]
    assert item.country_codes == ["NPL"] and item.correlation_id == "20260901-NPL-GEO-EAR-GRO-1-GCDB"
    assert item.geom is not None and item.occurred_at.year == 2026
    assert item.stac_url == (
        "https://montandon.example.org/stac/collections/reference-events/items/example-event-npl-earthquake-2026"
    )
    again, created2, changed2 = upsert_item(montandon, sample_item)
    assert again.pk == item.pk and not created2 and not changed2


def test_generic_profile_ignores_monty_fields(hazards, earth_search, sample_item):
    item, *_ = upsert_item(earth_search, sample_item)
    assert item.hazard is None and item.country_codes == [] and item.correlation_id == ""
    assert item.title.startswith("Earthquake in Nepal") and item.roles == ["event"]
    assert "monty:corr_id" not in item.as_stac_item()["properties"]


def test_same_item_id_is_distinct_per_catalog(hazards, montandon, earth_search, sample_item):
    a, *_ = upsert_item(montandon, sample_item)
    b, *_ = upsert_item(earth_search, sample_item)
    assert a.pk != b.pk and StacItem.objects.count() == 2


def test_rule_matching(hazards, montandon, earth_search, item_template, sample_item):
    item, *_ = upsert_item(montandon, sample_item)
    rule = TriggerRule.objects.create(
        name="r", catalog=montandon, notebook=item_template, collections=["reference-events"], roles=["event"]
    )
    assert rule_matches(rule, item)
    rule.hazards.set([hazards["flood"]])
    assert not rule_matches(rule, item)
    rule.hazards.set([hazards["earthquake"]])
    rule.country_codes = ["VNM"]
    assert not rule_matches(rule, item)
    rule.country_codes = ["npl"]
    assert rule_matches(rule, item)
    rule.collections = ["usgs-events"]
    assert not rule_matches(rule, item)
    rule.collections = ["reference-events"]
    rule.catalog = earth_search
    assert not rule_matches(rule, item)  # rules only see items from their own catalog


def test_render_parameters_with_jinja(hazards, montandon, sample_item):
    item, *_ = upsert_item(montandon, sample_item)
    out = render_parameters(
        {
            "corr": "{{ item.correlation_id }}",
            "legacy": "{{ event.correlation_id }}",
            "n": 3,
            "code": "{{ props['monty:hazard_codes'][0] }}",
            "h": "{{ hazard }}",
        },
        item,
    )
    assert out == {
        "corr": "20260901-NPL-GEO-EAR-GRO-1-GCDB",
        "legacy": "20260901-NPL-GEO-EAR-GRO-1-GCDB",
        "n": 3,
        "code": "nat-geo-ear-gro",
        "h": "earthquake",
    }


def test_fire_rules_country_level_and_dedupe(hazards, nepal, montandon, item_template, sample_item):
    item, *_ = upsert_item(montandon, sample_item)
    rule = TriggerRule.objects.create(
        name="r",
        catalog=montandon,
        notebook=item_template,
        collections=["reference-events"],
        parameter_template={"radius": 25},
    )
    assert fire_rules(item) == 1
    run = NotebookRun.objects.get()
    assert (
        run.area == nepal["root"]
        and run.stac_item == item
        and run.trigger == TriggerKind.AUTO
        and run.parameters["radius"] == 25
    )
    assert ItemRuleLink.objects.filter(item=item, rule=rule).exists()
    assert fire_rules(item) == 0  # already fired


def test_fire_rules_needs_countries_for_area_templates(
    hazards, nepal, earth_search, item_template, sample_item
):
    item, *_ = upsert_item(earth_search, sample_item)  # generic profile: no country codes
    TriggerRule.objects.create(
        name="r", catalog=earth_search, notebook=item_template, collections=["reference-events"]
    )
    assert fire_rules(item) == 0 and not NotebookRun.objects.exists()


def test_fire_rules_fan_out_over_level(hazards, nepal, montandon, item_template, sample_item):
    item, *_ = upsert_item(montandon, sample_item)
    TriggerRule.objects.create(
        name="r", catalog=montandon, notebook=item_template, collections=["reference-events"], area_level=2
    )
    assert fire_rules(item) == 4
    assert NotebookRun.objects.filter(batch__isnull=False).count() == 4


def test_rerun_on_update(hazards, nepal, montandon, item_template, sample_item):
    item, *_ = upsert_item(montandon, sample_item)
    TriggerRule.objects.create(
        name="r",
        catalog=montandon,
        notebook=item_template,
        collections=["reference-events"],
        rerun_on_update=True,
    )
    assert fire_rules(item) == 1
    updated = copy.deepcopy(sample_item)
    updated["properties"]["updated"] = "2026-09-02T00:00:00Z"
    item, created, changed = upsert_item(montandon, updated)
    assert changed and not created
    assert fire_rules(item, created=False, changed=True) == 1
    assert NotebookRun.objects.count() == 2


class FakeClient:
    def __init__(self, items, fail=False):
        self.items = items
        self.fail = fail
        self.calls = []

    def search(self, collections, **kwargs):
        self.calls.append((collections, kwargs))
        if self.fail:
            raise StacClientError("boom")
        return iter([i for i in self.items if i["collection"] in collections])


def test_poll_uses_state_per_catalog_and_creates_runs(hazards, nepal, montandon, item_template, sample_item):
    TriggerRule.objects.create(
        name="r",
        catalog=montandon,
        notebook=item_template,
        collections=["reference-events"],
        search_filter={"op": "=", "args": [{"property": "roles"}, "event"]},
    )
    client = FakeClient([sample_item])
    summary = poll(clients={montandon.pk: client})
    assert (
        summary["items_new"] == 1
        and summary["runs_created"] == 1
        and summary["collections"] == {"montandon:reference-events": 1}
    )
    assert client.calls[0][1]["filter"] == {"op": "=", "args": [{"property": "roles"}, "event"]}
    state = PollerState.objects.get(catalog=montandon, collection="reference-events")
    assert state.last_item_datetime == NotebookRun.objects.get().stac_item.occurred_at
    summary2 = poll(clients={montandon.pk: client})
    assert summary2["items_new"] == 0 and summary2["runs_created"] == 0
    # second poll starts from the last seen datetime (minus overlap)
    assert client.calls[1][1]["since"] < state.last_item_datetime


def test_poll_records_errors_on_catalog(hazards, montandon, earth_search, item_template, sample_item):
    TriggerRule.objects.create(
        name="m", catalog=montandon, notebook=item_template, collections=["reference-events"]
    )
    TriggerRule.objects.create(
        name="e", catalog=earth_search, notebook=item_template, collections=["sentinel-2-l2a"]
    )
    summary = poll(clients={montandon.pk: FakeClient([]), earth_search.pk: FakeClient([], fail=True)})
    assert summary["errors"] == ["earth-search:sentinel-2-l2a: boom"]
    earth_search.refresh_from_db()
    montandon.refresh_from_db()
    assert "boom" in earth_search.last_error and montandon.last_error == ""
    assert PollerState.objects.get(catalog=earth_search).last_error == "boom"


def _mock_transport(handler):
    original = httpx.Client.__init__

    def init(self, *args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        original(self, *args, **kwargs)

    return init


def test_client_sends_bearer_and_follows_pagination(monkeypatch):
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path, request.headers.get("authorization")))
        if request.url.path.endswith("/search") and request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "features": [{"id": "a"}],
                    "links": [{"rel": "next", "href": "https://x.org/v1/search?token=2"}],
                },
            )
        return httpx.Response(200, json={"features": [{"id": "b"}], "links": []})

    monkeypatch.setattr(httpx.Client, "__init__", _mock_transport(handler))
    with StacClient("https://x.org/v1/", "k") as client:
        ids = [f["id"] for f in client.search(["c"], ids=["a", "b"])]
    assert ids == ["a", "b"]
    assert seen[0] == ("POST", "/v1/search", "Bearer k") and seen[1][0] == "GET"


def test_client_without_key_sends_no_auth_and_wraps_errors(monkeypatch):
    def handler(request):
        assert "authorization" not in request.headers
        return httpx.Response(404, text="nope")

    monkeypatch.setattr(httpx.Client, "__init__", _mock_transport(handler))
    with pytest.raises(StacClientError, match="404"):
        StacClient("https://x.org/v1").get_item("c", "i")


def test_fetch_item_stores_item(monkeypatch, montandon, hazards, sample_item):
    def handler(request):
        assert (
            request.url.path == "/stac/collections/reference-events/items/example-event-npl-earthquake-2026"
        )
        assert request.headers["authorization"] == "Bearer monty-secret"
        return httpx.Response(200, json=sample_item)

    monkeypatch.setattr(httpx.Client, "__init__", _mock_transport(handler))
    item = fetch_item(montandon, "reference-events", "example-event-npl-earthquake-2026")
    assert item.catalog == montandon and item.hazard == hazards["earthquake"]
