"""
Give every existing template a v1 TemplateVersion, and turn local templates into
uploaded bundles: each notebook_templates/<slug>/ folder is zipped into the
published storage, so templates no longer depend on the image's templates dir.

Fail-soft: a template whose folder is missing, or whose bundle cannot be stored,
keeps `source_kind=local` (it still runs from the templates dir exactly as before)
and can be converted later with `sync_templates --convert`. Runs from before this
migration keep `template_version=NULL`.
"""

from pathlib import Path, PurePosixPath

from django.conf import settings
from django.db import migrations

SNAPSHOT_FIELDS = (
    "notebook_path",
    "source_repo",
    "source_ref",
    "parameter_schema",
    "requires_area",
    "area_levels",
    "requires_stac_item",
    "stac_hints",
    "supports_writeback",
    "estimated_runtime_minutes",
)


def _bundle(nb, root: Path):
    """(archive fields, notebook path in the bundle) for a local template, or None."""
    from factory.notebooks import bundles

    rel = PurePosixPath(nb.notebook_path)
    folder = root / rel.parent
    if not rel.parent.parts or not (folder / rel.name).is_file():
        print(f"\n  {nb.slug}: {folder / rel.name} not found; left as a local template")
        return None
    try:
        bundle = bundles.zip_folder(folder)
        key = bundles.bundle_key(nb.slug, 1, bundle.sha256)
        bundles.save(bundle, key)
    except Exception as exc:  # storage unavailable: keep the template working from disk
        print(f"\n  {nb.slug}: could not store its bundle ({exc}); left as a local template")
        return None
    return {"archive_key": key, "sha256": bundle.sha256, "files": bundle.files}, rel.name


def forwards(apps, schema_editor):
    AnalysisNotebook = apps.get_model("notebooks", "AnalysisNotebook")
    TemplateVersion = apps.get_model("notebooks", "TemplateVersion")
    root = Path(settings.NOTEBOOK_FACTORY["TEMPLATES_DIR"])
    for nb in AnalysisNotebook.objects.all():
        if TemplateVersion.objects.filter(notebook=nb).exists():
            continue
        fields = {f: getattr(nb, f) for f in SNAPSHOT_FIELDS}
        archive = {}
        if nb.source_kind == "local":
            found = _bundle(nb, root)
            if found:
                archive, fields["notebook_path"] = found
                fields["source_repo"] = fields["source_ref"] = ""
        version = TemplateVersion.objects.create(
            notebook=nb,
            number=1,
            label=nb.version,
            note="Created when versioning was introduced",
            **fields,
            **archive,
        )
        nb.current_version = version
        if archive:
            nb.source_kind = "bundle"
            nb.notebook_path = fields["notebook_path"]
            nb.source_repo = nb.source_ref = ""
        nb.save(
            update_fields=["current_version", "source_kind", "notebook_path", "source_repo", "source_ref"]
        )


def backwards(apps, schema_editor):
    """Back to templates-dir templates: restore the folder-relative path of bundles made by `forwards`."""
    AnalysisNotebook = apps.get_model("notebooks", "AnalysisNotebook")
    TemplateVersion = apps.get_model("notebooks", "TemplateVersion")
    NotebookRun = apps.get_model("notebooks", "NotebookRun")
    for nb in AnalysisNotebook.objects.exclude(current_version=None):
        if nb.source_kind == "bundle":
            nb.source_kind = "local"
            nb.notebook_path = f"{nb.slug}/{PurePosixPath(nb.notebook_path).name}"
        nb.current_version = None
        nb.save(update_fields=["current_version", "source_kind", "notebook_path"])
    NotebookRun.objects.exclude(template_version=None).update(template_version=None)
    TemplateVersion.objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [("notebooks", "0007_template_versions")]

    operations = [migrations.RunPython(forwards, backwards)]
