from django import forms

from factory.catalog.models import AdminArea, Country, Facet, FacetOption
from factory.events.matching import fetch_item
from factory.events.models import StacCatalog, StacItem
from factory.events.stac_client import StacClientError
from factory.notebooks import parameters as P
from factory.notebooks.models import NotebookRun
from factory.notebooks.uploads import UploadError, as_archive, inspect_archive


def resolve_stac_item(data) -> StacItem | None:
    """A picked (catalog, collection, item id) is fetched live and stored; else a pre-selected StacItem."""
    catalog, collection = data.get("stac_catalog"), (data.get("stac_collection") or "").strip()
    item_id = (data.get("stac_item_id") or "").strip()
    if catalog and collection and item_id:
        existing = data.get("stac_item")
        if existing and (existing.catalog_id, existing.collection, existing.item_id) == (
            catalog.pk,
            collection,
            item_id,
        ):
            return existing
        try:
            return fetch_item(catalog, collection, item_id)
        except StacClientError as exc:
            raise forms.ValidationError(f"Could not fetch {item_id} from {catalog}: {exc}") from exc
    if item_id and not (catalog and collection):
        raise forms.ValidationError("Pick a catalog and a collection for the STAC item.")
    return data.get("stac_item")


class RunRequestForm(forms.Form):
    """Area, STAC item + notification part of the run wizard. Parameter fields are added dynamically."""

    country = forms.ModelChoiceField(queryset=Country.objects.none(), required=False)
    level = forms.IntegerField(required=False, min_value=0, max_value=5)
    area_ids = forms.CharField(
        required=False, widget=forms.HiddenInput, help_text="Comma separated selected area ids"
    )
    all_at_level = forms.BooleanField(required=False, label="Run for every area at this level")
    stac_item = forms.ModelChoiceField(queryset=StacItem.objects.all(), required=False)
    stac_catalog = forms.ModelChoiceField(queryset=StacCatalog.objects.filter(is_active=True), required=False)
    stac_collection = forms.CharField(required=False, max_length=120)
    stac_item_id = forms.CharField(required=False, max_length=200)
    notify_emails = forms.CharField(required=False, label="Also notify (emails, comma separated)")

    def __init__(self, notebook, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.notebook = notebook
        self.fields["country"].queryset = Country.objects.filter(areas__isnull=False).distinct()
        self.param_fields = P.schema_fields(notebook.parameter_schema)

    def clean_notify_emails(self):
        raw = self.cleaned_data.get("notify_emails", "") or ""
        emails = [e.strip() for e in raw.replace(";", ",").split(",") if e.strip()]
        for e in emails:
            forms.EmailField().clean(e)
        return emails

    def clean(self):
        data = super().clean()
        nb = self.notebook
        areas = []
        if nb.requires_area:
            country = data.get("country")
            level = data.get("level")
            if data.get("all_at_level"):
                if country is None or level is None:
                    raise forms.ValidationError("Pick a country and an admin level to run for every area.")
                areas = list(AdminArea.objects.filter(country=country, level=level).light())
            else:
                ids = [int(x) for x in (data.get("area_ids") or "").split(",") if x.strip().isdigit()]
                areas = list(AdminArea.objects.filter(pk__in=ids).light())
            if not areas:
                raise forms.ValidationError("Select at least one area on the map.")
            if nb.area_levels:
                bad = [a for a in areas if a.level not in nb.area_levels]
                if bad:
                    raise forms.ValidationError(f"This template accepts admin levels {nb.area_levels} only.")
        data["stac_item"] = resolve_stac_item(data)
        if nb.requires_stac_item and not data["stac_item"]:
            raise forms.ValidationError(
                "This template needs a STAC item: pick a catalog, collection and item."
            )
        data["areas"] = areas
        return data


class UploadForm(forms.Form):
    """An already executed notebook (zip or .ipynb) with its metadata. Filter tags are posted separately."""

    file = forms.FileField(
        label="Notebook archive",
        help_text="A .zip with the notebook and the files it links to, or a single .ipynb",
    )
    notebook_path = forms.CharField(required=False, max_length=300, label="Notebook to publish")
    title = forms.CharField(
        required=False, max_length=200, help_text="Defaults to the notebook's first heading"
    )
    summary = forms.CharField(required=False, max_length=300, help_text="One line, shown on cards")
    description = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 4}), help_text="Markdown allowed"
    )
    country = forms.ModelChoiceField(queryset=Country.objects.none(), required=False)
    level = forms.IntegerField(required=False, min_value=0, max_value=5)
    area_ids = forms.CharField(required=False, widget=forms.HiddenInput)
    stac_catalog = forms.ModelChoiceField(queryset=StacCatalog.objects.filter(is_active=True), required=False)
    stac_collection = forms.CharField(required=False, max_length=120)
    stac_item_id = forms.CharField(required=False, max_length=200)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["country"].queryset = Country.objects.filter(areas__isnull=False).distinct()
        self.manifest = None

    def clean(self):
        data = super().clean()
        ids = [int(x) for x in (data.get("area_ids") or "").split(",") if x.strip().isdigit()]
        if len(ids) > 1:
            raise forms.ValidationError("An uploaded notebook can be about one area at most.")
        data["area"] = AdminArea.objects.light().filter(pk__in=ids).first() if ids else None
        data["stac_item"] = resolve_stac_item(data)
        upload = data.get("file")
        if upload is not None:
            try:
                data["archive"] = as_archive(upload, upload.name)
                self.manifest = inspect_archive(data["archive"], data.get("notebook_path") or "")
            except UploadError as exc:
                self.add_error("file", str(exc))
                return data
            if self.manifest.notebook is None:
                self.add_error(
                    "notebook_path",
                    "The archive holds several notebooks; pick one: " + ", ".join(self.manifest.notebooks),
                )
        return data


class RunDetailsForm(forms.ModelForm):
    class Meta:
        model = NotebookRun
        fields = ["display_title", "summary", "description"]
        labels = {"display_title": "Title"}
        widgets = {"description": forms.Textarea(attrs={"rows": 4})}


class StacCatalogForm(forms.ModelForm):
    """Staff form for a STAC catalog. A blank API key on edit keeps the stored one."""

    api_key = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "off"}),
        help_text="Sent as 'Authorization: Bearer <key>'. Leave blank to keep the current key.",
    )
    clear_api_key = forms.BooleanField(required=False, label="Remove the stored API key")

    class Meta:
        model = StacCatalog
        fields = ["name", "slug", "url", "api_key", "profile", "writeback_enabled", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not (self.instance.pk and self.instance.api_key):
            del self.fields["clear_api_key"]

    def clean_api_key(self):
        key = (self.cleaned_data.get("api_key") or "").strip()
        if not key and self.instance.pk:
            return self.instance.api_key
        return key

    def clean(self):
        data = super().clean()
        if data.get("clear_api_key"):
            data["api_key"] = ""
        return data


class FacetForm(forms.ModelForm):
    class Meta:
        model = Facet
        fields = ["label", "key", "description", "multiple", "show_in_browse", "stac_property", "order"]


class FacetOptionForm(forms.ModelForm):
    """match_codes as a comma separated list instead of raw JSON."""

    match_codes = forms.CharField(
        required=False, help_text="Comma separated code prefixes, e.g. nat-hyd-flo, nat-hyd-mmw"
    )

    class Meta:
        model = FacetOption
        fields = ["label", "key", "icon", "color", "match_codes", "order"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.initial["match_codes"] = ", ".join(self.instance.match_codes or [])

    def clean_match_codes(self):
        raw = self.cleaned_data.get("match_codes") or ""
        return [c.strip() for c in raw.split(",") if c.strip()]


FacetOptionFormSet = forms.inlineformset_factory(
    Facet, FacetOption, form=FacetOptionForm, extra=3, can_delete=True
)
