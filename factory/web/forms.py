from django import forms

from factory.catalog.models import AdminArea, Country
from factory.events.models import Event
from factory.notebooks import parameters as P


class RunRequestForm(forms.Form):
    """Area + notification part of the run wizard. Parameter fields are added dynamically."""

    country = forms.ModelChoiceField(queryset=Country.objects.none(), required=False)
    level = forms.IntegerField(required=False, min_value=0, max_value=5)
    area_ids = forms.CharField(
        required=False, widget=forms.HiddenInput, help_text="Comma separated selected area ids"
    )
    all_at_level = forms.BooleanField(required=False, label="Run for every area at this level")
    event = forms.ModelChoiceField(queryset=Event.objects.all(), required=False)
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
        if nb.requires_event and not data.get("event"):
            raise forms.ValidationError("This template needs a Montandon event.")
        data["areas"] = areas
        return data
