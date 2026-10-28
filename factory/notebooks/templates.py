"""
Create and version notebook templates. Used by the staff pages, the API,
`sync_templates` and the admin, so versioned fields are only ever written here.

A TemplateVersion freezes the bundle and the fields in SNAPSHOT_FIELDS; the
template keeps a copy of its current version's values, which is what runs,
forms and filters read. Changing only the schema or inputs makes a new version
that reuses the current bundle.
"""

from __future__ import annotations

import io
import logging
import zipfile
from pathlib import Path, PurePosixPath

import yaml
from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.utils.text import slugify

from factory.catalog.facets import option_keys, options_by_keys
from factory.catalog.models import Facet

from . import bundles
from . import parameters as P
from . import schema_builder as SB
from .models import SNAPSHOT_FIELDS, AnalysisNotebook, SourceKind, TemplateVersion
from .uploads import read_notebook

log = logging.getLogger(__name__)

DISPLAY_FIELDS = ("title", "summary", "description", "is_active")


class TemplateError(ValueError):
    pass


# -- template.yml --------------------------------------------------------------------
def meta_from_yaml(meta: dict, slug: str = "") -> tuple[dict, dict]:
    """(display fields, snapshot) from a template.yml dict. The snapshot's notebook_path is the file name."""
    display = {
        "title": meta.get("title") or slug,
        "summary": meta.get("summary", "") or "",
        "description": meta.get("description", "") or "",
        "is_active": bool(meta.get("active", True)),
    }
    snapshot = {
        "notebook_path": meta.get("notebook", "notebook.ipynb"),
        "source_repo": meta.get("source_repo", "") or "",
        "source_ref": meta.get("source_ref", "") or "",
        "parameter_schema": meta.get("parameters", {}) or {},
        "requires_area": bool(meta.get("requires_area", True)),
        "area_levels": meta.get("area_levels", []) or [],
        "requires_stac_item": bool(meta.get("requires_stac_item", meta.get("requires_event", False))),
        "stac_hints": meta.get("stac_hints", {}) or {},
        "supports_writeback": bool(meta.get("supports_writeback", False)),
        "estimated_runtime_minutes": meta.get("estimated_runtime_minutes"),
        "label": str(meta.get("version", "") or ""),
    }
    return display, snapshot


def options_from_yaml(meta: dict, slug: str = "") -> list:
    """Facet options named in template.yml; unknown filters / options are logged and skipped."""
    wanted = dict(meta.get("filters") or {})
    if meta.get("hazards") and "hazard" not in wanted:
        wanted["hazard"] = meta["hazards"]
    if meta.get("use_case") and "use_case" not in wanted:
        wanted["use_case"] = [meta["use_case"]]
    options = []
    facets = {f.key: f for f in Facet.objects.filter(key__in=list(wanted))}
    for key, values in wanted.items():
        if key not in facets:
            log.warning("template %s: unknown filter %r ignored", slug, key)
            continue
        options.extend(options_by_keys(facets[key], values if isinstance(values, list) else [values]))
    return options


def to_yaml(notebook: AnalysisNotebook) -> str:
    """The template's metadata as template.yml (what sync_templates and the upload form read back)."""
    meta = {
        "title": notebook.title,
        "summary": notebook.summary,
        "description": notebook.description,
        "filters": option_keys(notebook.facet_options.select_related("facet")),
        "notebook": PurePosixPath(notebook.notebook_path).name,
        "version": notebook.version,
        "requires_area": notebook.requires_area,
        "area_levels": notebook.area_levels,
        "requires_stac_item": notebook.requires_stac_item,
        "stac_hints": notebook.stac_hints,
        "supports_writeback": notebook.supports_writeback,
        "estimated_runtime_minutes": notebook.estimated_runtime_minutes,
        "active": notebook.is_active,
        "parameters": notebook.parameter_schema,
    }
    meta = {k: v for k, v in meta.items() if v not in ("", [], {}, None)}
    return yaml.safe_dump(meta, sort_keys=False, allow_unicode=True, width=110)


# -- versions -------------------------------------------------------------------------
def _clean_snapshot(snapshot: dict) -> dict:
    out = {f: snapshot[f] for f in SNAPSHOT_FIELDS if f in snapshot}
    out["label"] = str(snapshot.get("label", "") or "")
    return out


def snapshot_differs(notebook: AnalysisNotebook, snapshot: dict) -> bool:
    """Compared with the stored current version (a bound ModelForm may have changed the instance in memory)."""
    current = notebook.current_version.snapshot() if notebook.current_version else notebook.snapshot()
    return any(current.get(k) != v for k, v in _clean_snapshot(snapshot).items())


@transaction.atomic
def add_version(
    notebook: AnalysisNotebook,
    snapshot: dict,
    *,
    bundle: bundles.Bundle | None = None,
    user=None,
    note: str = "",
    make_current: bool = True,
) -> TemplateVersion:
    """A new version of `notebook`. Without a bundle, the current version's bundle is reused."""
    AnalysisNotebook.objects.select_for_update().filter(pk=notebook.pk).first()
    base = notebook.current_version
    fields = {**(base.snapshot() if base else notebook.snapshot()), **_clean_snapshot(snapshot)}
    number = (notebook.versions.aggregate(n=Max("number"))["n"] or 0) + 1
    archive = {}
    if bundle is not None:
        same = notebook.versions.filter(sha256=bundle.sha256).exclude(archive_key="").first()
        key = same.archive_key if same else bundles.bundle_key(notebook.slug, number, bundle.sha256)
        if not same:
            bundles.save(bundle, key)
        archive = {"archive_key": key, "sha256": bundle.sha256, "files": bundle.files}
    elif base is not None:
        archive = {"archive_key": base.archive_key, "sha256": base.sha256, "files": base.files}
    if not fields.get("notebook_path"):
        raise TemplateError("The version has no notebook path.")
    if archive.get("files") and fields["notebook_path"] not in archive["files"]:
        raise TemplateError(f"{fields['notebook_path']} is not in the bundle.")
    version = TemplateVersion.objects.create(
        notebook=notebook,
        number=number,
        note=note[:300],
        created_by=user if getattr(user, "is_authenticated", False) else None,
        **fields,
        **archive,
    )
    if make_current:
        notebook.make_current(version)
    return version


@transaction.atomic
def save_settings(
    notebook: AnalysisNotebook, display: dict, snapshot: dict, *, options=None, user=None, note: str = ""
) -> str:
    """Save the editor's changes. Returns "unchanged", "amended" or "new version".

    A version no run has used yet is amended in place (so configuring a fresh upload
    doesn't pile up versions); once used, it is frozen and a new version is made.
    """
    for key, value in display.items():
        if key in DISPLAY_FIELDS:
            setattr(notebook, key, value)
    notebook.save()
    if options is not None:
        notebook.facet_options.set(options)
    if not snapshot_differs(notebook, snapshot):
        return "unchanged"
    current = notebook.current_version
    if current is not None and not current.runs.exists():
        for key, value in _clean_snapshot(snapshot).items():
            setattr(current, key, value)
        if current.files and current.notebook_path not in current.files:
            raise TemplateError(f"{current.notebook_path} is not in the bundle.")
        if note:
            current.note = note[:300]
        current.save()
        notebook.make_current(current)
        return "amended"
    add_version(notebook, snapshot, user=user, note=note or "Settings changed")
    return "new version"


def set_current(notebook: AnalysisNotebook, version: TemplateVersion) -> None:
    if version.notebook_id != notebook.pk:
        raise TemplateError("That version belongs to another template.")
    notebook.make_current(version)


@transaction.atomic
def create_template(
    slug: str,
    display: dict,
    snapshot: dict,
    *,
    bundle: bundles.Bundle,
    options=(),
    user=None,
    note: str = "",
) -> AnalysisNotebook:
    if AnalysisNotebook.objects.filter(slug=slug).exists():
        raise TemplateError(f"A template with the slug {slug!r} already exists.")
    notebook = AnalysisNotebook.objects.create(
        slug=slug,
        source_kind=SourceKind.BUNDLE,
        **{k: v for k, v in display.items() if k in DISPLAY_FIELDS},
        **{k: v for k, v in _clean_snapshot(snapshot).items() if k != "label"},
        version=str(snapshot.get("label", "") or ""),
    )
    add_version(notebook, snapshot, bundle=bundle, user=user, note=note or "Uploaded")
    notebook.facet_options.set(options)
    return notebook


def convert_local(notebook: AnalysisNotebook, root: Path | None = None, user=None) -> TemplateVersion | None:
    """Bundle a local template's folder into a new current version (None when the folder is missing)."""
    root = Path(root or settings.NOTEBOOK_FACTORY["TEMPLATES_DIR"])
    rel = PurePosixPath(notebook.notebook_path)
    folder = root / rel.parent
    if notebook.source_kind != SourceKind.LOCAL or not rel.parent.parts or not (folder / rel.name).is_file():
        return None
    return add_version(
        notebook,
        {**notebook.snapshot(), "notebook_path": rel.name, "source_repo": "", "source_ref": ""},
        bundle=bundles.zip_folder(folder),
        user=user,
        note="Bundled from the templates folder",
    )


# -- uploads -------------------------------------------------------------------------------
def schema_for_manifest(
    manifest: bundles.TemplateManifest, base: dict | None = None
) -> tuple[dict, list[str]]:
    """The parameter schema for an uploaded notebook, and warnings.

    template.yml's `parameters` win when present; otherwise the parameters cell is
    detected and merged into `base` (the current schema, for a new version).
    """
    detected, warnings = SB.detect_parameters(manifest.nb)
    if "parameters" in manifest.meta:
        schema = manifest.meta.get("parameters") or {}
        declared = set(schema.get("properties") or {})
        extra = [d.name for d in detected if d.name not in declared]
        if extra:
            warnings.append(
                "In the parameters cell but not in template.yml (so not in the form): "
                + ", ".join(f"`{n}`" for n in extra)
                + "."
            )
        return schema, warnings
    rows, merge_warnings = SB.merge_detected(SB.rows_from_schema(base or {}), detected)
    return SB.schema_from_rows(rows), [*warnings, *merge_warnings]


def create_from_manifest(
    manifest: bundles.TemplateManifest, *, slug: str = "", title: str = "", user=None, note: str = ""
) -> tuple[AnalysisNotebook, list[str]]:
    """A new, inactive template from an inspected upload (staff then review it in the editor)."""
    meta = manifest.meta
    title = (
        title
        or meta.get("title")
        or manifest.title
        or PurePosixPath(manifest.notebook).stem.replace("_", " ")
    )
    slug = slug or meta.get("slug") or slugify(title)[:80] or "template"
    display, snapshot = meta_from_yaml({**meta, "title": title}, slug)
    schema, warnings = schema_for_manifest(manifest)
    snapshot.update(
        {"notebook_path": manifest.notebook, "parameter_schema": schema, "source_repo": "", "source_ref": ""}
    )
    display["is_active"] = False
    notebook = create_template(
        slug,
        display,
        snapshot,
        bundle=manifest.bundle,
        options=options_from_yaml(meta, slug),
        user=user,
        note=note or "Uploaded",
    )
    return notebook, [*manifest.warnings, *warnings]


def add_version_from_manifest(
    notebook: AnalysisNotebook,
    manifest: bundles.TemplateManifest,
    *,
    user=None,
    note: str = "",
    make_current: bool = True,
) -> tuple[TemplateVersion, list[str]]:
    """A new version from an inspected upload: template.yml settings if present, else the current ones."""
    current = notebook.current_version
    snapshot = current.snapshot() if current else notebook.snapshot()
    if manifest.meta:
        snapshot.update(meta_from_yaml(manifest.meta, notebook.slug)[1])
    schema, warnings = schema_for_manifest(manifest, base=notebook.parameter_schema)
    errors, schema_warnings = SB.check_schema(schema)
    if errors:
        raise TemplateError(
            "The parameters are not valid: " + "; ".join(f"{k}: {v}" for k, v in errors.items())
        )
    snapshot.update(
        {"notebook_path": manifest.notebook, "parameter_schema": schema, "source_repo": "", "source_ref": ""}
    )
    warnings = [*manifest.warnings, *warnings, *schema_warnings]
    same = notebook.versions.filter(sha256=manifest.bundle.sha256).order_by("-number").first()
    if same:
        warnings.append(f"The files are identical to v{same.number}.")
    version = add_version(
        notebook,
        snapshot,
        bundle=manifest.bundle,
        user=user,
        note=note or "New upload",
        make_current=make_current,
    )
    if make_current:
        warnings.extend(trigger_rule_problems(notebook, schema))
        queued = notebook.runs.active().exclude(template_version=version).count()
        if queued:
            warnings.append(
                f"{queued} queued or running run(s) keep using the version they were started with."
            )
    return version, warnings


def current_notebook(notebook: AnalysisNotebook):
    """The template's notebook (nbformat), read from its current bundle or the templates dir."""
    version = notebook.current_version
    raw = None
    if version is not None and version.archive_key:
        raw = dict(bundles.read_entries(io.BytesIO(bundles.load(version.archive_key)))).get(
            notebook.notebook_path
        )
    elif notebook.source_kind == SourceKind.LOCAL:
        path = Path(settings.NOTEBOOK_FACTORY["TEMPLATES_DIR"]) / notebook.notebook_path
        raw = path.read_bytes() if path.is_file() else None
    if raw is None:
        raise TemplateError("The template's notebook could not be read.")
    return read_notebook(raw, notebook.notebook_path)


# -- export / delete -------------------------------------------------------------------
def export_bundle(notebook: AnalysisNotebook, version: TemplateVersion | None = None) -> bytes:
    """The bundle as a zip, with a regenerated template.yml next to the notebook."""
    version = version or notebook.current_version
    if version is not None and version.archive_key:
        entries = bundles.read_entries(io.BytesIO(bundles.load(version.archive_key)))
        nb_path = version.notebook_path
    elif notebook.source_kind == SourceKind.LOCAL:
        rel = PurePosixPath(notebook.notebook_path)
        folder = Path(settings.NOTEBOOK_FACTORY["TEMPLATES_DIR"]) / rel.parent
        if not (folder / rel.name).is_file():
            raise TemplateError("The template's folder is missing.")
        bundle = bundles.zip_folder(folder)
        entries = bundles.read_entries(io.BytesIO(bundle.data))
        nb_path = rel.name
    else:
        raise TemplateError("Git templates are exported from their repository.")
    meta_path = (PurePosixPath(nb_path).parent / bundles.META_FILE).as_posix().removeprefix("./")
    entries = [(n, d) for n, d in entries if n != meta_path]
    entries.append((meta_path, to_yaml(notebook).encode()))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in sorted(entries):
            zf.writestr(name, data)
    return buf.getvalue()


def delete_template(notebook: AnalysisNotebook) -> None:
    """Delete a template that has never run (its trigger rules and batches go with it)."""
    if notebook.runs.exists():
        raise TemplateError("This template has runs; deactivate it instead.")
    keys = set(notebook.versions.exclude(archive_key="").values_list("archive_key", flat=True))
    with transaction.atomic():
        AnalysisNotebook.objects.filter(pk=notebook.pk).update(current_version=None)
        notebook.delete()
    for key in keys:
        try:
            bundles.storage().delete(key)
        except Exception:  # best effort: an orphaned zip is harmless
            log.warning("could not delete bundle %s", key, exc_info=True)


# -- impact checks ---------------------------------------------------------------------
def _is_jinja(value) -> bool:
    return isinstance(value, str) and ("{{" in value or "{%" in value)


def trigger_rule_problems(notebook: AnalysisNotebook, schema: dict) -> list[str]:
    """Trigger rules whose fixed parameters would not validate against `schema`."""
    out = []
    for rule in notebook.trigger_rules.all():
        params = rule.parameter_template or {}
        templated = {k for k, v in params.items() if _is_jinja(v)}
        try:
            P.validate(schema, {k: v for k, v in params.items() if k not in templated})
        except P.ParameterError as exc:
            errors = {
                k: v
                for k, v in exc.errors.items()
                if k not in templated and not (v == "This parameter is required." and k in templated)
            }
            if errors:
                detail = "; ".join(f"{k}: {v}" for k, v in errors.items())
                out.append(f"Trigger rule “{rule.name}”{'' if rule.enabled else ' (disabled)'}: {detail}")
    return out
