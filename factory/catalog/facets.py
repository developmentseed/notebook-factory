"""
Filters (facets) and their options: how templates, runs and STAC items get
tagged, and how querysets are filtered by tags.

A run's options for each facet come from the first source that yields any, in
this order: manual (set by a user) > a template parameter > the STAC item >
the template (only when it lists exactly one option for that facet). Facets
that are not `multiple` keep only the first option.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from itertools import groupby

from django.db.models import QuerySet

from .models import Facet, FacetOption

log = logging.getLogger(__name__)

SOURCE_ORDER = ["manual", "parameter", "stac_item", "template"]


def _as_list(value) -> list:
    if value in (None, ""):
        return []
    if isinstance(value, list | tuple | set):
        return [v for v in value if v not in (None, "")]
    return [value]


def group_options(options: Iterable[FacetOption]) -> list[tuple[Facet, list[FacetOption]]]:
    """[(facet, [options])] in facet then option order; expects option.facet to be loaded."""
    ordered = sorted(options, key=lambda o: (o.facet.order, o.facet.label, o.facet_id, o.order, o.label))
    return [(facet, list(opts)) for facet, opts in groupby(ordered, key=lambda o: o.facet)]


def option_keys(options: Iterable[FacetOption]) -> dict[str, list[str]]:
    """{facet_key: [option_key, ...]} for passing to notebooks, Jinja and the API."""
    return {facet.key: [o.key for o in opts] for facet, opts in group_options(options)}


def options_by_keys(facet: Facet, keys: Iterable) -> list[FacetOption]:
    keys = [str(k) for k in _as_list(list(keys))]
    found = {o.key: o for o in facet.options.filter(key__in=keys)}
    missing = [k for k in keys if k not in found]
    if missing:
        log.info("unknown %s option(s) ignored: %s", facet.key, ", ".join(missing))
    return [found[k] for k in keys if k in found]


# -- sources -------------------------------------------------------------------
def from_parameters(notebook, params: Mapping) -> dict[Facet, list[FacetOption]]:
    """Options named by template parameters.

    A parameter feeds a facet when its schema declares `x-filter: <facet key>`,
    or when its name equals a facet key (so a `hazard` parameter tags the hazard).
    """
    props = (notebook.parameter_schema or {}).get("properties", {}) or {}
    facets = {f.key: f for f in Facet.objects.all()}
    out: dict[Facet, list[FacetOption]] = {}
    for name, value in (params or {}).items():
        key = (props.get(name) or {}).get("x-filter") or name
        facet = facets.get(key)
        if facet is None or not _as_list(value):
            continue
        options = options_by_keys(facet, _as_list(value))
        if options:
            out.setdefault(facet, []).extend(o for o in options if o not in out.get(facet, []))
    return out


def parameter_facet_keys(notebook) -> set[str]:
    """Facet keys that a template parameter feeds (those are not offered as manual tags in the run form)."""
    props = (notebook.parameter_schema or {}).get("properties", {}) or {}
    facet_keys = set(Facet.objects.values_list("key", flat=True))
    return {(spec or {}).get("x-filter") or name for name, spec in props.items()} & facet_keys


def from_template(notebook) -> dict[Facet, list[FacetOption]]:
    """The template's option for each facet where it lists exactly one."""
    return {
        facet: opts
        for facet, opts in group_options(notebook.facet_options.select_related("facet"))
        if len(opts) == 1
    }


def from_stac_item(stac_item) -> dict[Facet, list[FacetOption]]:
    if stac_item is None:
        return {}
    return dict(group_options(stac_item.facet_options.select_related("facet")))


def from_manual(manual: Mapping[str, Iterable] | None) -> dict[Facet, list[FacetOption]]:
    """{facet_key: [option_key, ...]} as posted by a form or the API."""
    out = {}
    for facet in Facet.objects.filter(key__in=list((manual or {}).keys())):
        options = options_by_keys(facet, manual[facet.key])
        if options:
            out[facet] = options
    return out


def for_stac_item(raw: dict) -> list[FacetOption]:
    """Options matched by each facet's `stac_property` in a raw STAC item (any catalog)."""
    props = raw.get("properties", {}) or {}
    matched: list[FacetOption] = []
    for facet in Facet.objects.exclude(stac_property="").prefetch_related("options"):
        values = _as_list(props.get(facet.stac_property))
        if not values:
            continue
        options = [o for o in facet.options.all() if any(o.matches(v) for v in values)]
        matched.extend(options if facet.multiple else options[:1])
    return matched


# -- runs ----------------------------------------------------------------------
def resolve_run(notebook, params, stac_item=None, manual=None) -> list[tuple[FacetOption, str]]:
    """[(option, source)] for a new run; per facet the highest-priority source wins."""
    sources = {
        "manual": from_manual(manual),
        "parameter": from_parameters(notebook, params),
        "stac_item": from_stac_item(stac_item),
        "template": from_template(notebook),
    }
    chosen: dict[Facet, tuple[list[FacetOption], str]] = {}
    for source in SOURCE_ORDER:
        for facet, options in sources[source].items():
            if facet not in chosen and options:
                chosen[facet] = (options if facet.multiple else options[:1], source)
    return [(o, source) for options, source in chosen.values() for o in options]


def set_run_facets(run, resolved: list[tuple[FacetOption, str]]) -> None:
    from factory.notebooks.models import RunFacet

    RunFacet.objects.filter(run=run).delete()
    RunFacet.objects.bulk_create([RunFacet(run=run, option=o, source=s) for o, s in resolved])


def update_run_manual(run, manual: Mapping[str, Iterable]) -> None:
    """Replace the manual tags of the facets in `manual`; an empty list falls back to derived tags."""
    merged = manual_tags(run)
    for key, values in manual.items():
        merged[key] = _as_list(list(values))
    merged = {k: v for k, v in merged.items() if v}
    set_run_facets(run, resolve_run(run.notebook, run.parameters, run.stac_item, merged))


def manual_tags(run) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for rf in run.run_facets.select_related("option__facet").filter(source="manual"):
        out.setdefault(rf.option.facet.key, []).append(rf.option.key)
    return out


# -- filtering -----------------------------------------------------------------
def selected(params: Mapping, facets: Iterable[Facet] | None = None) -> dict[Facet, list[str]]:
    """{facet: [option keys]} picked in a QueryDict / dict, keyed by facet key."""
    facets = list(facets) if facets is not None else list(Facet.objects.all())
    out = {}
    for facet in facets:
        if hasattr(params, "getlist"):
            values = [v for v in params.getlist(facet.key) if v]
        else:
            values = _as_list(params.get(facet.key))
        if values:
            out[facet] = values
    return out


def filter_queryset(qs: QuerySet, params: Mapping, rel: str = "facet_options") -> QuerySet:
    """Filter by every facet present in `params`: any of its options, and all facets together."""
    chosen = selected(params)
    for facet, keys in chosen.items():
        qs = qs.filter(**{f"{rel}__facet": facet, f"{rel}__key__in": keys})
    return qs.distinct() if chosen else qs
