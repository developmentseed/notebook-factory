import json
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.contrib.gis.geos import MultiPolygon, Polygon

from factory.catalog.models import AdminArea, Country, Hazard
from factory.notebooks.models import AnalysisNotebook

FIXTURES = Path(__file__).resolve().parent.parent / "examples" / "events"


@pytest.fixture
def hazards(db):
    return {
        "flood": Hazard.objects.create(
            key="flood", label="Flood", icon="🌊", monty_codes=["nat-hyd-flo"], order=1
        ),
        "earthquake": Hazard.objects.create(
            key="earthquake", label="Earthquake", icon="🏚️", monty_codes=["nat-geo-ear"], order=2
        ),
    }


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
def template(db, hazards):
    nb = AnalysisNotebook.objects.create(
        slug="exposure",
        title="Exposure",
        use_case="uc1",
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
    nb.hazards.set(hazards.values())
    return nb


@pytest.fixture
def event_template(db, hazards):
    return AnalysisNotebook.objects.create(
        slug="impact",
        title="Impact",
        use_case="uc2",
        notebook_path="impact/notebook.ipynb",
        requires_area=True,
        requires_event=True,
        parameter_schema={"type": "object", "properties": {"radius": {"type": "number", "default": 10}}},
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
