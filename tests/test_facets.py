import pytest
from django.core.exceptions import ValidationError

from factory.catalog import facets as F
from factory.catalog.models import Facet, FacetOption
from factory.events.matching import upsert_item
from factory.notebooks.models import NotebookRun
from factory.notebooks.services import create_run


@pytest.fixture
def sector(db):
    facet = Facet.objects.create(key="sector", label="Sector", multiple=True, order=30)
    return {
        "facet": facet,
        "health": FacetOption.objects.create(facet=facet, key="health", label="Health"),
        "shelter": FacetOption.objects.create(facet=facet, key="shelter", label="Shelter"),
        "wash": FacetOption.objects.create(facet=facet, key="wash", label="WASH"),
    }


def _tags(run):
    return {rf.option.key: rf.source for rf in run.run_facets.select_related("option")}


def test_parameter_beats_template_and_manual_beats_everything(template, nepal, facets):
    run = create_run(template, {"hazard": "flood"}, area=nepal["a1"])
    assert _tags(run) == {"flood": "parameter", "uc1": "template"}
    run = create_run(template, {"hazard": "flood"}, area=nepal["a1"], filters={"hazard": ["earthquake"]})
    assert _tags(run) == {"earthquake": "manual", "uc1": "template"}


def test_template_with_several_options_does_not_tag(item_template, nepal, facets, montandon, sample_item):
    # item_template lists two hazards, so the hazard comes from the STAC item
    item, *_ = upsert_item(montandon, sample_item)
    run = create_run(item_template, {}, area=nepal["root"], stac_item=item)
    assert _tags(run) == {"earthquake": "stac_item", "uc2": "template"}
    run = create_run(item_template, {}, area=nepal["root"], stac_item=item, filters={"hazard": ["flood"]})
    assert _tags(run)["flood"] == "manual"


def test_single_facet_keeps_one_option_multiple_keeps_all(template, nepal, facets, sector):
    run = create_run(
        template,
        {"hazard": "flood"},
        area=nepal["a1"],
        filters={"use_case": ["uc1", "uc2"], "sector": ["health", "wash", "nope"]},
    )
    assert run.filter_values == {"hazard": ["flood"], "use_case": ["uc1"], "sector": ["health", "wash"]}


def test_x_filter_maps_a_differently_named_parameter(template, nepal, facets, sector):
    template.parameter_schema["properties"]["focus"] = {
        "type": "array",
        "items": {"type": "string", "enum": ["health", "shelter"]},
        "x-filter": "sector",
    }
    template.save()
    run = create_run(template, {"hazard": "flood", "focus": ["shelter", "health"]}, area=nepal["a1"])
    assert run.filter_values["sector"] == ["health", "shelter"]
    assert F.parameter_facet_keys(template) == {"hazard", "sector"}


def test_stac_property_prefix_matching(facets):
    raw = {"id": "x", "properties": {"monty:hazard_codes": ["nat-geo-ear-gro", "unknown"]}}
    assert F.for_stac_item(raw) == [facets["earthquake"]]
    assert F.for_stac_item({"id": "x", "properties": {"monty:hazard_codes": "flood"}}) == [facets["flood"]]
    assert F.for_stac_item({"id": "x", "properties": {}}) == []


def test_filter_queryset_and_across_or_within(template, nepal, facets, sector):
    a = create_run(template, {"hazard": "flood"}, area=nepal["a1"], filters={"sector": ["health"]})
    b = create_run(template, {"hazard": "earthquake"}, area=nepal["a1"], filters={"sector": ["wash"]})
    c = create_run(template, {"hazard": "flood"}, area=nepal["a1"])
    qs = NotebookRun.objects.all()
    from django.http import QueryDict

    def ids(query):
        return set(F.filter_queryset(qs, QueryDict(query)).values_list("pk", flat=True))

    assert ids("hazard=flood") == {a.pk, c.pk}
    assert ids("hazard=flood&hazard=earthquake") == {a.pk, b.pk, c.pk}
    assert ids("hazard=flood&sector=health") == {a.pk}
    assert ids("sector=health&sector=wash&use_case=uc1") == {a.pk, b.pk}
    assert ids("") == {a.pk, b.pk, c.pk}


def test_update_run_manual_overrides_and_falls_back(template, nepal, facets, sector):
    run = create_run(template, {"hazard": "flood"}, area=nepal["a1"])
    F.update_run_manual(run, {"hazard": ["earthquake"], "sector": ["shelter"]})
    assert _tags(run) == {"earthquake": "manual", "shelter": "manual", "uc1": "template"}
    F.update_run_manual(run, {"hazard": []})  # cleared → back to the parameter, sector kept
    assert _tags(run) == {"flood": "parameter", "shelter": "manual", "uc1": "template"}


def test_reserved_keys_are_rejected(db):
    with pytest.raises(ValidationError):
        Facet(key="status", label="Status").full_clean()
