"""Adapt the existing Django run form to the public React workspace."""

from django.template.loader import render_to_string

from factory.catalog.models import AdminArea


def run_config(request, context):
    """Serialize choices and escaped Django field partials without duplicating validation rules.

    Only our own autoescaped templates become HTML slots. User and catalog values must never
    be marked safe here. Native form submission returns bound values and errors on this route.
    """
    form, notebook = context["form"], context["nb"]
    levels = {}
    for country, level in AdminArea.objects.order_by().values_list("country_id", "level").distinct():
        if not notebook.area_levels or level in notebook.area_levels:
            levels.setdefault(country, []).append(level)
    countries = [
        {"iso3": c.iso3, "name": c.name, "levels": sorted(levels[c.iso3])}
        for c in context["countries"]
        if c.iso3 in levels
    ]
    country = form["country"].value()
    return {
        "slug": notebook.slug,
        "title": notebook.title,
        "requiresArea": notebook.requires_area,
        "countries": countries,
        "country": str(country or ""),
        "level": form["level"].value(),
        "areaIds": context["selected_area_ids"],
        "allAtLevel": bool(form["all_at_level"].value()),
        "optionsOpen": any(name in form.errors for name in ("notify_emails", "event_refs")),
        "runtime": notebook.estimated_runtime_minutes,
        "errors": [str(error) for errors in form.errors.values() for error in errors]
        + list(context["param_errors"].values()),
        "parametersHtml": render_to_string("web/partials/param_fields.html", context, request),
        "optionsHtml": render_to_string("web/partials/run_options.html", context, request),
        "stacHtml": render_to_string("web/partials/stac_picker.html", context, request)
        if notebook.requires_stac_item
        else None,
        "stacItem": str(form["stac_item"].value() or ""),
        "hasParameters": bool(context["fields"]),
    }
