import pytest
from django.http import QueryDict

from factory.notebooks import parameters as P

SCHEMA = {
    "type": "object",
    "properties": {
        "hazard": {"type": "string", "enum": ["flood", "cyclone"], "default": "flood", "title": "Hazard"},
        "threshold": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.5, "multipleOf": 0.05},
        "period": {"type": "integer", "enum": [10, 100], "default": 100},
        "layers": {
            "type": "array",
            "items": {"type": "string", "enum": ["schools", "hospitals"]},
            "default": [],
        },
        "verbose": {"type": "boolean", "default": False},
        "note": {"type": "string"},
    },
    "required": ["hazard", "threshold"],
}


def test_schema_fields_infer_widgets():
    fields = {f.name: f for f in P.schema_fields(SCHEMA)}
    assert fields["hazard"].widget == "select" and fields["hazard"].choices[0] == ("flood", "flood")
    assert fields["layers"].widget == "checkboxes" and fields["layers"].multiple
    assert fields["verbose"].widget == "checkbox"
    assert fields["threshold"].widget == "number" and fields["threshold"].step == 0.05
    assert fields["hazard"].required and not fields["note"].required


def test_coerce_form_data_types():
    q = QueryDict("hazard=cyclone&threshold=0.6&period=10&layers=schools&layers=hospitals&verbose=on&note=")
    out = P.coerce_form_data(SCHEMA, q)
    assert out == {
        "hazard": "cyclone",
        "threshold": 0.6,
        "period": 10,
        "layers": ["schools", "hospitals"],
        "verbose": True,
    }


def test_validate_fills_defaults_and_tolerates_float_steps():
    out = P.validate(SCHEMA, {"hazard": "flood", "threshold": 0.6})
    assert out["period"] == 100 and out["layers"] == [] and out["verbose"] is False


def test_validate_reports_errors_per_field():
    with pytest.raises(P.ParameterError) as exc:
        P.validate(SCHEMA, {"hazard": "lava", "threshold": 3})
    assert set(exc.value.errors) == {"hazard", "threshold"}


def test_validate_missing_required():
    schema = {"type": "object", "properties": {"note": {"type": "string"}}, "required": ["note"]}
    with pytest.raises(P.ParameterError) as exc:
        P.validate(schema, {})
    assert "note" in exc.value.errors


def test_display_values_use_schema_titles_and_labels_in_name_order():
    schema = {
        "properties": {
            "hazard": {
                "type": "string",
                "title": "Hazard",
                "enum": ["flood"],
                "x-enum-labels": {"flood": "Riverine flood"},
            },
            "layers": {"type": "array", "items": {"enum": ["a", "b"], "x-enum-labels": {"a": "Schools"}}},
            "detailed": {"type": "boolean"},
            "note": {"type": "string"},
        }
    }
    rows = P.display_values(
        schema, {"extra_value": 3, "detailed": False, "layers": ["a", "b"], "hazard": "flood", "note": ""}
    )
    assert [(row["label"], row["value"]) for row in rows] == [
        ("Detailed", "No"),
        ("Extra value", "3"),
        ("Hazard", "Riverine flood"),
        ("Layers", "Schools, b"),
    ]
