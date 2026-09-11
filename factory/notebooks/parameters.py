"""
Parameter schemas.

Templates declare their parameters as a JSON Schema object. This module turns
that schema into form fields for the UI, coerces submitted form data back into
typed values, and validates them.

Supported per-property keys (a pragmatic subset of JSON Schema):
  type: string | number | integer | boolean | array   (array items must have enum)
  enum, default, title, description, minimum, maximum, multipleOf, minItems, maxItems
  x-widget: select | radio | checkboxes | slider | textarea   (optional UI hint)
  x-enum-labels: {value: label}                                  (optional)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import jsonschema


class ParameterError(ValueError):
    def __init__(self, errors: dict[str, str]):
        self.errors = errors
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))


@dataclass
class Field:
    name: str
    title: str
    type: str
    widget: str
    description: str = ""
    required: bool = False
    default: Any = None
    choices: list[tuple[Any, str]] = field(default_factory=list)
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    multiple: bool = False

    @property
    def input_type(self) -> str:
        return {"number": "number", "integer": "number", "boolean": "checkbox"}.get(self.type, "text")


def schema_fields(schema: dict) -> list[Field]:
    props = (schema or {}).get("properties", {}) or {}
    required = set((schema or {}).get("required", []) or [])
    out = []
    for name, spec in props.items():
        spec = spec or {}
        ptype = spec.get("type", "string")
        if isinstance(ptype, list):
            ptype = next((t for t in ptype if t != "null"), "string")
        labels = spec.get("x-enum-labels", {}) or {}
        enum = spec.get("enum")
        multiple = ptype == "array"
        if multiple:
            enum = (spec.get("items") or {}).get("enum", [])
            labels = (spec.get("items") or {}).get("x-enum-labels", labels)
        choices = [(v, str(labels.get(str(v), labels.get(v, v)))) for v in (enum or [])]
        widget = spec.get("x-widget")
        if not widget:
            if multiple:
                widget = "checkboxes"
            elif enum:
                widget = "select"
            elif ptype == "boolean":
                widget = "checkbox"
            elif ptype in ("number", "integer"):
                widget = "number"
            else:
                widget = "text"
        out.append(
            Field(
                name=name,
                title=spec.get("title", name.replace("_", " ").capitalize()),
                type=ptype,
                widget=widget,
                description=spec.get("description", ""),
                required=name in required,
                default=spec.get("default"),
                choices=choices,
                minimum=spec.get("minimum"),
                maximum=spec.get("maximum"),
                step=spec.get("multipleOf"),
                multiple=multiple,
            )
        )
    return out


def defaults(schema: dict) -> dict[str, Any]:
    return {f.name: f.default for f in schema_fields(schema) if f.default is not None}


def _coerce_scalar(f: Field, raw: str | None) -> Any:
    if raw is None or raw == "":
        return None
    if f.type == "integer":
        return int(float(raw))
    if f.type == "number":
        return float(raw)
    if f.type == "boolean":
        return str(raw).lower() in {"1", "true", "on", "yes"}
    # keep enum values typed as declared
    for value, _ in f.choices:
        if str(value) == str(raw):
            return value
    return raw


def coerce_form_data(schema: dict, data) -> dict[str, Any]:
    """Turn a QueryDict / dict of strings into typed parameter values (unvalidated)."""
    out: dict[str, Any] = {}
    getlist = getattr(data, "getlist", None)
    for f in schema_fields(schema):
        if f.multiple:
            raw = getlist(f.name) if getlist else data.get(f.name, [])
            if isinstance(raw, str):
                raw = [raw]
            typed = []
            for r in raw or []:
                for value, _ in f.choices:
                    if str(value) == str(r):
                        typed.append(value)
                        break
            out[f.name] = typed
        elif f.type == "boolean":
            raw = data.get(f.name)
            out[f.name] = _coerce_scalar(f, raw) if raw is not None else False
        else:
            raw = data.get(f.name)
            try:
                value = _coerce_scalar(f, raw)
            except (TypeError, ValueError):
                value = raw
            if value is None and f.default is not None:
                value = f.default
            if value is not None:
                out[f.name] = value
    return out


def _for_validation(schema: dict) -> dict:
    """`multipleOf` is only a UI step hint here: float arithmetic makes it fail for values like 0.6 / 0.05."""
    props = {}
    for name, spec in (schema.get("properties") or {}).items():
        props[name] = {k: v for k, v in (spec or {}).items() if k != "multipleOf"}
    return {**schema, "properties": props}


def validate(schema: dict, params: dict) -> dict[str, Any]:
    """Validate parameters against the schema; fill defaults. Raises ParameterError."""
    params = dict(params or {})
    for key, value in defaults(schema).items():
        params.setdefault(key, value)
    if not schema:
        return params
    validator = jsonschema.Draft202012Validator({"type": "object", **_for_validation(schema)})
    errors: dict[str, str] = {}
    for err in validator.iter_errors(params):
        path = ".".join(str(p) for p in err.path) or (
            err.validator_value[0] if err.validator == "required" else "_"
        )
        if err.validator == "required":
            for missing in err.validator_value:
                if missing not in params:
                    errors[missing] = "This parameter is required."
        else:
            errors[path] = err.message
    if errors:
        raise ParameterError(errors)
    return params
