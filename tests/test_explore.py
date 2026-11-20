from datetime import UTC, datetime

import pytest
from rest_framework.pagination import PageNumberPagination

from factory.api.views import NotebookRunViewSet
from factory.events.models import EventLinkOverride, StacCatalog, StacItem
from factory.notebooks.models import AnalysisNotebook, NotebookRun, RunFacet, RunStatus

pytestmark = pytest.mark.django_db
URL = "/api/runs/events/"


@pytest.fixture
def event(montandon):
    return StacItem.objects.create(
        catalog=montandon,
        collection="reference-events",
        item_id="nepal-flood",
        correlation_id="nepal-flood-2026",
        title="Flood in Nepal 2026",
        roles=["event"],
        country_codes=["NPL"],
        occurred_at=datetime(2026, 8, 25, tzinfo=UTC),
    )


@pytest.fixture
def make_run(template, nepal):
    def create(event=None, status=RunStatus.PUBLISHED, notebook=None, **kwargs):
        return NotebookRun.objects.create(
            notebook=notebook or template,
            area=nepal["a1"],
            stac_item=event,
            status=status,
            output_url="/published/example/index.html",
            **kwargs,
        )

    return create


def test_event_card_contains_only_published_matching_analyses(client, event, make_run):
    first = make_run(event)
    second = make_run(event)
    make_run(event, status=RunStatus.FAILED)
    make_run()
    response = client.get(URL)
    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 1 and data["analysis_count"] == 2
    card = data["results"][0]
    assert card["title"] == "Flood in Nepal 2026"
    assert card["purposes"] == ["Exposure"]
    assert card["date"].startswith("2026-08-25")
    assert card["analysis_count"] == 2
    assert {row["id"] for row in card["analyses"]} == {str(first.pk), str(second.pk)}
    assert card["analyses"][0]["viewer_url"].endswith("/view/")
    assert "api_key" not in response.content.decode()


def test_shared_correlation_ids_group_only_within_one_catalog(client, event, montandon, make_run):
    make_run(event)
    related = StacItem.objects.create(
        catalog=montandon,
        collection="hazards",
        item_id="nepal-flood-extent",
        correlation_id=event.correlation_id,
    )
    make_run(related)
    other_catalog = StacCatalog.objects.create(name="Other Montandon", slug="other", profile="montandon")
    separate = StacItem.objects.create(
        catalog=other_catalog,
        collection="reference-events",
        item_id=event.item_id,
        correlation_id=event.correlation_id,
    )
    make_run(separate)
    data = client.get(URL).json()
    assert data["count"] == 2
    assert sorted(card["analysis_count"] for card in data["results"]) == [1, 2]


def test_empty_correlation_ids_do_not_merge_unrelated_items(client, event, montandon, make_run):
    event.correlation_id = ""
    event.save()
    another = StacItem.objects.create(catalog=montandon, collection="reference-events", item_id="another")
    make_run(event)
    make_run(another)
    assert client.get(URL).json()["count"] == 2


def test_filters_change_card_contents_counts_and_purposes(client, event, make_run, hazards):
    flood = make_run(event)
    other_template = AnalysisNotebook.objects.create(slug="impact", title="Impact estimation")
    earthquake = make_run(event, notebook=other_template)
    RunFacet.objects.create(run=flood, option=hazards["flood"], source="manual")
    RunFacet.objects.create(run=earthquake, option=hazards["earthquake"], source="manual")
    data = client.get(URL, {"hazard": "flood"}).json()
    assert data["analysis_count"] == 1
    assert data["results"][0]["analysis_count"] == 1
    assert data["results"][0]["purposes"] == ["Exposure"]
    assert client.get(URL, {"notebook": "impact"}).json()["results"][0]["purposes"] == ["Impact estimation"]
    assert client.get(URL, {"country": "VNM"}).json()["count"] == 0
    assert client.get(URL, {"country": "NPL"}).json()["count"] == 1
    assert client.get(URL, {"search": "nothing matches"}).json()["count"] == 0
    assert client.get(URL, {"status": "failed"}).json()["count"] == 0


def test_non_montandon_and_unviewable_runs_are_excluded(client, event, make_run, earth_search):
    unpublished = make_run(event)
    unpublished.output_url = ""
    unpublished.save()
    generic = StacItem.objects.create(catalog=earth_search, collection="events", item_id="event")
    make_run(generic)
    data = client.get(URL).json()
    assert data["count"] == 0 and data["analysis_count"] == 0 and data["results"] == []


def test_repeated_hazard_matches_do_not_duplicate_analyses(client, event, make_run, hazards):
    run = make_run(event)
    for option in hazards.values():
        RunFacet.objects.create(run=run, option=option, source="manual")
    data = client.get(URL, {"hazard": ["flood", "earthquake"]}).json()
    assert data["analysis_count"] == 1
    assert data["results"][0]["analysis_count"] == 1
    assert len(data["results"][0]["analyses"]) == 1


def test_pagination_keeps_all_analyses_of_an_event_together(client, event, montandon, make_run, monkeypatch):
    class SmallPage(PageNumberPagination):
        page_size = 1

    monkeypatch.setattr(NotebookRunViewSet, "pagination_class", SmallPage)
    for _ in range(3):
        make_run(event)
    another = StacItem.objects.create(catalog=montandon, collection="reference-events", item_id="another")
    make_run(another)
    data = client.get(URL).json()
    assert data["count"] == 2 and data["analysis_count"] == 4 and data["next"]
    assert len(data["results"]) == 1 and data["results"][0]["analysis_count"] == 3
    assert len(data["results"][0]["analyses"]) == 3
    second = client.get(URL, {"page": 2}).json()
    assert second["previous"] and second["results"][0]["analysis_count"] == 1


def test_reference_linked_analyses_appear_once_in_event_cards(client, event, make_run):
    event.event_refs = ["glide:FL-2026-000001-NPL", "glide:FL-2026-000001"]
    event.save()
    linked = make_run(event_refs=["glide:FL-2026-000001-NPL", "glide:FL-2026-000001"])
    make_run(event_refs=["glide:FL-2099-000099"])
    direct = make_run(event, event_refs=["glide:FL-2026-000001"])
    data = client.get(URL).json()
    assert data["count"] == 1 and data["analysis_count"] == 2
    assert {row["id"] for row in data["results"][0]["analyses"]} == {str(linked.pk), str(direct.pk)}


def test_episode_stems_group_within_catalog_only(client, event, montandon, make_run):
    event.correlation_id = "20260825-NPL-FL-1-GCDB"
    event.save()
    make_run(event)
    for catalog in [montandon, StacCatalog.objects.create(name="Other", slug="other", profile="montandon")]:
        item = StacItem.objects.create(
            catalog=catalog,
            collection="gdacs-events",
            item_id="gdacs-event-100-2",
            correlation_id="20260825-NPL-FL-2-GCDB",
        )
        make_run(item)
    data = client.get(URL).json()
    assert data["count"] == 2 and data["analysis_count"] == 3
    assert sorted(card["analysis_count"] for card in data["results"]) == [1, 2]


def test_event_metadata_is_independent_of_matching_analyses(client, event, montandon, make_run):
    hazard = StacItem.objects.create(
        catalog=montandon,
        collection="hazards",
        item_id="flood-extent",
        correlation_id=event.correlation_id,
        title="Flood extent raster",
        roles=["hazard"],
        occurred_at=datetime(2026, 8, 26, tzinfo=UTC),
    )
    impact = AnalysisNotebook.objects.create(slug="impact", title="Impact estimation")
    make_run(hazard, notebook=impact)
    # The event item has no analysis, but still supplies the card's metadata.
    data = client.get(URL, {"notebook": "impact"}).json()
    card = data["results"][0]
    assert card["title"] == event.title
    assert card["date"].startswith("2026-08-25")
    make_run(event)
    filtered = client.get(URL, {"notebook": "impact"}).json()["results"][0]
    unfiltered = client.get(URL).json()["results"][0]
    assert (filtered["id"], filtered["title"], filtered["date"]) == (
        unfiltered["id"],
        unfiltered["title"],
        unfiltered["date"],
    )
    assert filtered["analysis_count"] == 1 and unfiltered["analysis_count"] == 2


def test_shared_source_refs_merge_transitively(client, event, montandon, make_run):
    event.event_refs = ["gdacs:100"]
    event.save()
    bridge = StacItem.objects.create(
        catalog=montandon,
        collection="cems-events",
        item_id="cems-event-EMSR1",
        event_refs=["gdacs:100", "cems:EMSR1"],
    )
    sibling = StacItem.objects.create(
        catalog=montandon,
        collection="other-events",
        item_id="other-event-1",
        event_refs=["cems:EMSR1"],
    )
    for item in [event, bridge, sibling]:
        make_run(item)
    data = client.get(URL).json()
    assert data["count"] == 1 and data["results"][0]["analysis_count"] == 3


def test_analysis_linked_to_multiple_events_counts_once_overall(client, event, montandon, make_run):
    event.event_refs = ["gdacs:100"]
    event.save()
    StacItem.objects.create(
        catalog=montandon, collection="other-events", item_id="other-event-2", event_refs=["gdacs:200"]
    )
    run = make_run(event_refs=["gdacs:100", "gdacs:200"])
    data = client.get(URL).json()
    assert data["count"] == 2 and data["analysis_count"] == 1
    assert all(
        card["analysis_count"] == 1 and card["analyses"][0]["id"] == str(run.pk) for card in data["results"]
    )


def test_catalog_filter_scopes_reference_matches_and_keeps_reference_only_runs(client, event, make_run):
    event.event_refs = ["gdacs:100"]
    event.save()
    other_catalog = StacCatalog.objects.create(name="Other", slug="other", profile="montandon")
    StacItem.objects.create(
        catalog=other_catalog,
        collection=event.collection,
        item_id=event.item_id,
        correlation_id=event.correlation_id,
        event_refs=event.event_refs,
    )
    direct = make_run(event, event_refs=["gdacs:100"])
    linked = make_run(event_refs=["gdacs:100"])
    data = client.get(URL, {"catalog": event.catalog.slug}).json()
    assert data["count"] == 1 and data["analysis_count"] == 2
    assert data["results"][0]["catalog"] == event.catalog.slug
    assert {r["id"] for r in data["results"][0]["analyses"]} == {str(direct.pk), str(linked.pk)}
    unfiltered = client.get(URL).json()
    other = next(card for card in unfiltered["results"] if card["catalog"] == "other")
    assert {r["id"] for r in other["analyses"]} == {str(linked.pk)}


def test_staff_different_verdict_splits_shared_references_without_duplicate_ids(
    client, event, montandon, make_run
):
    from factory.events.refs import corr_refs

    event.event_refs = corr_refs(event.correlation_id)
    event.save()
    another = StacItem.objects.create(
        catalog=montandon,
        collection=event.collection,
        item_id="another-event",
        correlation_id=event.correlation_id,
        event_refs=event.event_refs,
    )
    first = make_run(event, event_refs=event.event_refs)
    second = make_run(another, event_refs=another.event_refs)
    EventLinkOverride.objects.create(
        catalog=montandon,
        disaster_ref=f"stac:{event.collection}/{event.item_id}",
        item_ref=f"stac:{another.collection}/{another.item_id}",
        verdict="different",
    )
    data = client.get(URL).json()
    assert data["count"] == 2 and data["analysis_count"] == 2
    assert len({card["id"] for card in data["results"]}) == 2
    assert {tuple(row["id"] for row in card["analyses"]) for card in data["results"]} == {
        (str(first.pk),),
        (str(second.pk),),
    }


def test_staff_different_verdict_survives_transitive_shared_reference(client, event, montandon, make_run):
    event.event_refs = ["gdacs:100"]
    event.save()
    bridge = StacItem.objects.create(
        catalog=montandon,
        collection="events",
        item_id="bridge",
        event_refs=["gdacs:100", "cems:EMSR1"],
    )
    excluded = StacItem.objects.create(
        catalog=montandon,
        collection="events",
        item_id="excluded",
        event_refs=["cems:EMSR1"],
    )
    for item in [event, bridge, excluded]:
        make_run(item)
    EventLinkOverride.objects.create(
        catalog=montandon,
        disaster_ref=f"stac:{event.collection}/{event.item_id}",
        item_ref="stac:events/excluded",
        verdict="different",
    )
    data = client.get(URL).json()
    assert data["count"] == 2
    assert sorted(card["analysis_count"] for card in data["results"]) == [1, 2]


def test_staff_same_verdict_joins_unrelated_stored_items(client, event, montandon, make_run):
    other = StacItem.objects.create(catalog=montandon, collection="events", item_id="other")
    make_run(event)
    make_run(other)
    EventLinkOverride.objects.create(
        catalog=montandon,
        disaster_ref=f"stac:{event.collection}/{event.item_id}",
        item_ref="stac:events/other",
        verdict="same",
    )
    data = client.get(URL).json()
    assert data["count"] == 1 and data["results"][0]["analysis_count"] == 2


def test_search_finds_reference_linked_analysis_by_event_title(client, event, make_run):
    linked = make_run(event_refs=[f"stac:{event.collection}/{event.item_id}"])
    data = client.get(URL, {"search": '"Flood in Nepal"'}).json()
    assert data["count"] == 1 and data["analysis_count"] == 1
    assert data["results"][0]["analyses"][0]["id"] == str(linked.pk)
    assert client.get(URL, {"search": "Nonexistent"}).json()["count"] == 0


def test_search_combines_event_metadata_with_matching_analysis_fields(client, event, make_run):
    first = make_run(event, display_title="School assessment")
    make_run(event, display_title="Hospital assessment")
    data = client.get(URL, {"search": "Flood School"}).json()
    assert data["analysis_count"] == 1
    assert data["results"][0]["analyses"][0]["id"] == str(first.pk)
    assert client.get(URL, {"search": "School Hospital"}).json()["count"] == 0
