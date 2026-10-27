import json
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import MultiPolygon, Polygon

from factory.catalog.models import AdminArea, Country, Facet, FacetOption
from factory.events.models import CatalogProfile, StacCatalog
from factory.notebooks.models import AnalysisNotebook

FIXTURES = Path(__file__).resolve().parent.parent / "examples" / "stac_items"


@pytest.fixture
def facets(db):
    """The two seeded filters: hazard (mapped from monty:hazard_codes) and use case."""
    # The data migration already creates both facets (and the use case options) in a fresh database.
    hazard, _ = Facet.objects.update_or_create(
        key="hazard", defaults={"label": "Hazard", "stac_property": "monty:hazard_codes", "order": 10}
    )
    use_case, _ = Facet.objects.update_or_create(key="use_case", defaults={"label": "Use case", "order": 20})

    def option(facet, key, label, **extra):
        return FacetOption.objects.update_or_create(facet=facet, key=key, defaults={"label": label, **extra})[
            0
        ]

    return {
        "hazard": hazard,
        "use_case": use_case,
        "flood": option(hazard, "flood", "Flood", icon="🌊", match_codes=["nat-hyd-flo"], order=1),
        "earthquake": option(
            hazard, "earthquake", "Earthquake", icon="🏚️", match_codes=["nat-geo-ear"], order=2
        ),
        "uc1": option(use_case, "uc1", "Risk exposure", order=1),
        "uc2": option(use_case, "uc2", "Impact estimation", order=2),
    }


@pytest.fixture
def hazards(facets):
    """Hazard filter options by key."""
    return {k: facets[k] for k in ("flood", "earthquake")}


def _square(x, y, size=1.0):
    return MultiPolygon(
        Polygon(((x, y), (x + size, y), (x + size, y + size), (x, y + size), (x, y))), srid=4326
    )


@pytest.fixture
def nepal(db):
    country = Country.objects.create(iso3="NPL", name="Nepal")
    root = AdminArea.objects.create(
        code="NPL", name="Nepal", level=0, country=country, geom=_square(84, 27, 2)
    )
    a1 = AdminArea.objects.create(
        code="NPL.1_1", name="West", level=1, country=country, parent=root, geom=_square(84, 27, 1)
    )
    a2 = AdminArea.objects.create(
        code="NPL.2_1", name="East", level=1, country=country, parent=root, geom=_square(85, 27, 1)
    )
    for i, parent in enumerate([a1, a2]):
        for j in range(2):
            AdminArea.objects.create(
                code=f"{parent.code}.{j}",
                name=f"{parent.name} district {j}",
                level=2,
                country=country,
                parent=parent,
                geom=_square(84 + i, 27 + j * 0.5, 0.5),
            )
    return {"country": country, "root": root, "a1": a1, "a2": a2}


@pytest.fixture
def template(db, facets, hazards):
    nb = AnalysisNotebook.objects.create(
        slug="exposure",
        title="Exposure",
        notebook_path="exposure/notebook.ipynb",
        parameter_schema={
            "type": "object",
            "properties": {
                "hazard": {"type": "string", "enum": ["flood", "earthquake"], "default": "flood"},
                "threshold": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                    "default": 0.5,
                    "multipleOf": 0.05,
                },
                "layers": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["schools", "hospitals"]},
                    "default": ["schools"],
                },
                "verbose": {"type": "boolean", "default": False},
            },
            "required": ["hazard"],
        },
        requires_area=True,
    )
    nb.facet_options.set([*hazards.values(), facets["uc1"]])
    return nb


@pytest.fixture
def item_template(db, facets, hazards):
    nb = AnalysisNotebook.objects.create(
        slug="impact",
        title="Impact",
        notebook_path="impact/notebook.ipynb",
        requires_area=True,
        requires_stac_item=True,
        stac_hints={"profile": "montandon", "collections": ["reference-events"], "roles": ["event"]},
        parameter_schema={"type": "object", "properties": {"radius": {"type": "number", "default": 10}}},
    )
    nb.facet_options.set([*hazards.values(), facets["uc2"]])
    return nb


@pytest.fixture
def montandon(db):
    return StacCatalog.objects.create(
        name="Montandon",
        slug="montandon",
        url="https://montandon.example.org/stac/",
        api_key="monty-secret",
        profile=CatalogProfile.MONTANDON,
    )


@pytest.fixture
def earth_search(db):
    return StacCatalog.objects.create(
        name="Earth Search", slug="earth-search", url="https://earth-search.example.org/v1"
    )


@pytest.fixture
def sample_item():
    return json.loads((FIXTURES / "nepal-earthquake.json").read_text())


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("ana", "ana@example.org", "pw")


@pytest.fixture(autouse=True)
def _no_enqueue(settings, monkeypatch):
    """Never start real pipelines from tests; assert on the queued runs instead."""
    settings.CELERY_TASK_ALWAYS_EAGER = True
    monkeypatch.setattr("factory.notebooks.services.enqueue_run", lambda run: None)
