"""
Build a template's parameter schema from its notebook, and edit it as rows.

`detect_parameters` reads the cell tagged `parameters` and infers one field per
`name = <literal>` assignment (a trailing `# comment` becomes the description).
`rows_from_schema` / `schema_from_rows` convert between the JSON Schema stored on
a template and the flat rows of the staff editor; keys the editor does not show
(e.g. `minItems`) ride along in each row's `extra` so nothing is lost.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import re
import sys
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

import jsonschema
from django.conf import settings

from . import parameters as P
from .pipeline.inputs import RESERVED_PARAMETERS

TYPES = ["string", "number", "integer", "boolean", "array"]
WIDGETS = ["", "select", "radio", "checkboxes", "slider", "textarea"]
# Reserved names a template may still declare as a user parameter (UC1 lets the user pick the hazard).
OVERRIDABLE = {"hazard"}
# Schema keys the row editor shows; anything else is kept in `extra`.
ROW_KEYS = {
    "type",
    "title",
    "description",
    "default",
    "enum",
    "items",
    "x-enum-labels",
    "minimum",
    "maximum",
    "multipleOf",
    "x-widget",
    "x-filter",
}


@dataclass
class DetectedParam:
    name: str
    type: str
    default: Any
    description: str = ""
    choices: list | None = None


# -- notebooks -------------------------------------------------------------------
def parameters_cell(nb):
    for cell in nb.cells:
        if cell.cell_type == "code" and "parameters" in (cell.get("metadata", {}).get("tags") or []):
            return cell
    return None


def _python(source: str) -> str:
    """Cell source with IPython magics / shell lines blanked (keeps line numbers)."""
    return "\n".join("" if line.lstrip().startswith(("%", "!")) else line for line in source.splitlines())


def _infer(value) -> tuple[str, list | None] | None:
    if isinstance(value, bool):
        return "boolean", None
    if isinstance(value, int):
        return "integer", None
    if isinstance(value, float):
        return "number", None
    if isinstance(value, str):
        return "string", None
    if isinstance(value, list | tuple) and all(isinstance(v, str | int | float) for v in value):
        return "array", list(value)
    return None


def detect_parameters(nb) -> tuple[list[DetectedParam], list[str]]:
    """User parameters declared in the parameters cell (reserved names skipped), and warnings."""
    cell = parameters_cell(nb)
    if cell is None:
        return [], []
    source = _python(cell.source)
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [], [f"The parameters cell does not parse: {exc.msg} (line {exc.lineno})."]
    lines = source.splitlines()
    found, warnings = [], []
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            name = node.target.id
        else:
            continue
        if name in RESERVED_PARAMETERS:
            continue
        try:
            value = ast.literal_eval(node.value)
        except ValueError:
            warnings.append(f"{name}: its default is not a literal, so it is not offered as a parameter.")
            continue
        inferred = _infer(value)
        if inferred is None:
            warnings.append(f"{name}: a {type(value).__name__} default cannot become a form field; skipped.")
            continue
        line = lines[node.end_lineno - 1][node.end_col_offset :]
        comment = line.split("#", 1)[1].strip() if "#" in line else ""
        ptype, choices = inferred
        found.append(DetectedParam(name, ptype, list(value) if ptype == "array" else value, comment, choices))
    return found, warnings


def _imports(nb) -> set[str]:
    names = set()
    for cell in nb.cells:
        if cell.cell_type != "code":
            continue
        try:
            tree = ast.parse(_python(cell.source))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names.add(node.module.split(".")[0])
    return names


def missing_modules(nb, bundle_files: list[str], notebook_path: str) -> list[str]:
    """Imported top-level modules that are neither shipped in the bundle nor installed here."""
    folder = PurePosixPath(notebook_path).parent
    local = set()
    for f in bundle_files:
        rel = PurePosixPath(f)
        if folder.parts and rel.parts[: len(folder.parts)] != folder.parts:
            continue
        rel = PurePosixPath(*rel.parts[len(folder.parts) :])
        if rel.parts:
            local.add(rel.parts[0].removesuffix(".py"))
    missing = []
    for name in sorted(_imports(nb) - local):
        if name in sys.stdlib_module_names:
            continue
        try:
            if importlib.util.find_spec(name) is None:
                missing.append(name)
        except (ImportError, ValueError):
            missing.append(name)
    return missing


def notebook_warnings(nb, bundle_files: list[str], notebook_path: str) -> list[str]:
    out = []
    cell = parameters_cell(nb)
    if cell is None:
        out.append(
            "No cell is tagged `parameters`: values are injected at the top, and nothing could be auto-detected."
        )
    else:
        declared = set(re.findall(r"^\s*([A-Za-z_]\w*)\s*[:=]", cell.source, flags=re.M))
        undeclared = [n for n in RESERVED_PARAMETERS if n not in declared and n != "hazard"]
        if undeclared:
            out.append(
                "The parameters cell does not declare "
                + ", ".join(f"`{n}`" for n in undeclared)
                + " (injected by the app; declare them so the notebook also runs standalone)."
            )
    kernel = (nb.metadata.get("kernelspec") or {}).get("name")
    if kernel and kernel != settings.NOTEBOOK_FACTORY["KERNEL_NAME"]:
        out.append(
            f"The notebook asks for kernel `{kernel}`; runs use `{settings.NOTEBOOK_FACTORY['KERNEL_NAME']}`."
        )
    missing = missing_modules(nb, bundle_files, notebook_path)
    if missing:
        out.append(
            "Not installed on the workers: "
            + ", ".join(f"`{m}`" for m in missing)
            + ". Runs will fail on import unless the image adds them."
        )
    if any(c.get("outputs") for c in nb.cells if c.cell_type == "code"):
        out.append(
            "The notebook still has outputs; they are replaced when it runs (clearing them keeps it small)."
        )
    return out


# -- rows <-> schema ---------------------------------------------------------------
def _fmt(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return ", ".join(_fmt(v) for v in value)
    return str(value)


def _choices_text(enum, labels) -> str:
    out = []
    for value in enum or []:
        label = (labels or {}).get(str(value), (labels or {}).get(value))
        out.append(f"{value} | {label}" if label not in (None, "", str(value)) else str(value))
    return "\n".join(out)


def rows_from_schema(schema: dict) -> list[dict]:
    schema = schema or {}
    required = set(schema.get("required") or [])
    rows = []
    for i, (name, spec) in enumerate((schema.get("properties") or {}).items()):
        spec = dict(spec or {})
        ptype = spec.get("type", "string")
        if isinstance(ptype, list):
            ptype = next((t for t in ptype if t != "null"), "string")
        items = dict(spec.get("items") or {})
        if ptype == "array":
            enum, labels = items.pop("enum", None), items.pop("x-enum-labels", spec.get("x-enum-labels"))
            item_type = items.pop("type", "string")
        else:
            enum, labels, item_type = spec.get("enum"), spec.get("x-enum-labels"), ""
        extra = {k: v for k, v in spec.items() if k not in ROW_KEYS}
        if items:
            extra["items"] = items
        rows.append(
            {
                "name": name,
                "include": True,
                "type": ptype,
                "item_type": item_type,
                "title": spec.get("title", ""),
                "description": spec.get("description", ""),
                "default": _fmt(spec.get("default")),
                "choices": _choices_text(enum, labels),
                "minimum": spec.get("minimum"),
                "maximum": spec.get("maximum"),
                "step": spec.get("multipleOf"),
                "widget": spec.get("x-widget", ""),
                "required": name in required,
                "filter": spec.get("x-filter", ""),
                "extra": json.dumps(extra) if extra else "",
                "order": i,
            }
        )
    return rows


def rows_from_detected(detected: list[DetectedParam], start: int = 0) -> list[dict]:
    rows = []
    for i, d in enumerate(detected):
        item_type = ""
        if d.type == "array":
            item_type = _infer(d.choices[0])[0] if d.choices else "string"
        rows.append(
            {
                "name": d.name,
                "include": True,
                "type": d.type,
                "item_type": item_type,
                "title": d.name.replace("_", " ").capitalize(),
                "description": d.description,
                "default": _fmt(d.default),
                "choices": _choices_text(d.choices, {}) if d.choices else "",
                "minimum": None,
                "maximum": None,
                "step": None,
                "widget": "",
                "required": False,
                "filter": "",
                "extra": "",
                "order": start + i,
            }
        )
    return rows


def merge_detected(rows: list[dict], detected: list[DetectedParam]) -> tuple[list[dict], list[str]]:
    """Keep the existing rows, add newly detected parameters, and warn about vanished ones."""
    known = {r["name"] for r in rows}
    found = {d.name for d in detected}
    added = rows_from_detected([d for d in detected if d.name not in known], start=len(rows))
    warnings = [
        f"`{r['name']}` is in the schema but no longer in the parameters cell."
        for r in rows
        if detected and r["name"] not in found
    ]
    return [*rows, *added], warnings


class RowError(ValueError):
    def __init__(self, errors: dict[str, str]):
        self.errors = errors
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))


def _typed(raw: str, ptype: str, name: str):
    raw = raw.strip()
    try:
        if ptype == "integer":
            return int(float(raw))
        if ptype == "number":
            return float(raw)
    except ValueError as exc:
        raise RowError({name: f"{raw!r} is not a {ptype}."}) from exc
    if ptype == "boolean":
        return raw.lower() in {"1", "true", "yes", "on"}
    return raw


def _parse_choices(text: str, ptype: str, name: str) -> tuple[list, dict]:
    enum, labels = [], {}
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        value, _, label = line.partition("|")
        typed = _typed(value, ptype, name)
        enum.append(typed)
        if label.strip():
            labels[str(typed)] = label.strip()
    return enum, labels


def schema_from_rows(rows: list[dict]) -> dict:
    """The JSON Schema for the included rows, in row order. Raises RowError for bad values."""
    props, required = {}, []
    for row in sorted((r for r in rows if r.get("include", True)), key=lambda r: r.get("order") or 0):
        name = (row.get("name") or "").strip()
        if not name:
            continue
        ptype = row.get("type") or "string"
        try:
            spec = json.loads(row["extra"]) if row.get("extra") else {}
        except json.JSONDecodeError as exc:
            raise RowError({name: f"extra keys are not valid JSON: {exc}"}) from exc
        spec["type"] = ptype
        for key in ("title", "description"):
            if (row.get(key) or "").strip():
                spec[key] = row[key].strip()
        if ptype == "array":
            item_type = row.get("item_type") or "string"
            enum, labels = _parse_choices(row.get("choices", ""), item_type, name)
            items = {**spec.get("items", {}), "type": item_type, "enum": enum}
            if labels:
                items["x-enum-labels"] = labels
            spec["items"] = items
            default = row.get("default", "")
            if default.strip():
                spec["default"] = [_typed(v, item_type, name) for v in default.split(",") if v.strip()]
        else:
            enum, labels = _parse_choices(row.get("choices", ""), ptype, name)
            if enum:
                spec["enum"] = enum
            if labels:
                spec["x-enum-labels"] = labels
            if (row.get("default") or "").strip():
                spec["default"] = _typed(row["default"], ptype, name)
        for row_key, key in (("minimum", "minimum"), ("maximum", "maximum"), ("step", "multipleOf")):
            if row.get(row_key) not in (None, ""):
                value = float(row[row_key])
                spec[key] = int(value) if ptype == "integer" and value.is_integer() else value
        if row.get("widget"):
            spec["x-widget"] = row["widget"]
        if row.get("filter"):
            spec["x-filter"] = row["filter"]
        props[name] = spec
        if row.get("required"):
            required.append(name)
    schema: dict = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    return schema


# -- validation --------------------------------------------------------------------
def check_schema(schema: dict) -> tuple[dict[str, str], list[str]]:
    """({parameter: error}, warnings) for a schema about to be saved on a template version."""
    errors: dict[str, str] = {}
    warnings: list[str] = []
    if not schema:
        return errors, warnings
    try:
        jsonschema.Draft202012Validator.check_schema(schema)
    except jsonschema.SchemaError as exc:
        errors["_schema"] = exc.message
        return errors, warnings
    props = schema.get("properties") or {}
    for name, spec in props.items():
        spec = spec or {}
        ptype = spec.get("type", "string")
        widget = spec.get("x-widget")
        if not re.fullmatch(r"[A-Za-z_]\w*", name):
            errors[name] = "Parameter names must be Python identifiers."
        elif name in RESERVED_PARAMETERS and name not in OVERRIDABLE:
            errors[name] = "This name is injected by the app and cannot be a user parameter."
        elif ptype not in TYPES:
            errors[name] = f"Unsupported type {ptype!r}."
        elif ptype == "array" and not (spec.get("items") or {}).get("enum"):
            errors[name] = "A list parameter needs choices."
        elif widget in ("select", "radio") and not spec.get("enum"):
            errors[name] = f"The {widget} widget needs choices."
        elif widget == "checkboxes" and ptype != "array":
            errors[name] = "Checkboxes are for list parameters."
        elif widget == "slider" and ptype not in ("number", "integer"):
            errors[name] = "Sliders are for numbers."
    for name in schema.get("required") or []:
        if name not in props:
            errors[name] = "Required but not defined."
    if not errors:
        try:
            P.validate(schema, {})
        except P.ParameterError as exc:
            for name, message in exc.errors.items():
                if message != "This parameter is required.":
                    errors[name] = f"Default: {message}"
    warnings.extend(filter_warnings(schema))
    return errors, warnings


def filter_warnings(schema: dict) -> list[str]:
    """Parameters that feed a filter but offer values that are not that filter's option keys."""
    from factory.catalog.models import Facet

    facets = {f.key: f for f in Facet.objects.prefetch_related("options")}
    out = []
    for name, spec in ((schema or {}).get("properties") or {}).items():
        spec = spec or {}
        facet = facets.get(spec.get("x-filter") or name)
        if facet is None:
            if spec.get("x-filter"):
                out.append(f"`{name}` links to an unknown filter `{spec['x-filter']}`.")
            continue
        values = spec.get("enum") or (spec.get("items") or {}).get("enum") or []
        keys = {o.key for o in facet.options.all()}
        unknown = [str(v) for v in values if str(v) not in keys]
        if unknown:
            out.append(
                f"`{name}` tags runs with the {facet.label} filter, which has no option "
                + ", ".join(f"`{v}`" for v in unknown)
                + "."
            )
    return out
