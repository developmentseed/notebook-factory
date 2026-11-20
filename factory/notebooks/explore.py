"""Build Explore cards from stored Montandon identities without live catalog requests."""

from collections import defaultdict
from datetime import UTC, datetime

from django.db.models import Q
from django.urls import reverse

from factory.events import refs as event_refs
from factory.events.models import CatalogProfile, EventLinkOverride, LinkVerdict, StacItem


def event_runs(queryset):
    """Keep viewable results; event membership is resolved from both item and run references."""
    return queryset.published().exclude(output_url="")


def _stored_events(catalog=None):
    """Index catalog-local connected references, including episode stems and explicit source links.

    Metadata comes from all stored members, independently of the current run filters. Only exact
    stored references are used: Explore does not perform geographic matching or contact a catalog.
    Staff SAME verdicts join stored items; DIFFERENT verdicts block both direct and transitive joins.
    """
    queryset = StacItem.objects.filter(catalog__profile=CatalogProfile.MONTANDON)
    if catalog:
        queryset = queryset.filter(catalog__slug=catalog)
    items = list(
        queryset.select_related("catalog")
        .only(
            "catalog_id",
            "catalog__slug",
            "collection",
            "item_id",
            "correlation_id",
            "title",
            "roles",
            "occurred_at",
            "event_refs",
        )
        .order_by("pk")
    )
    parents = {item.pk: item.pk for item in items}
    references = defaultdict(set)
    item_refs = {}
    components = {item.pk: {item.pk} for item in items}
    blocked = defaultdict(set)

    def root(key):
        """Find a connected component, compressing paths for subsequent reference lookups."""
        while parents[key] != key:
            parents[key] = parents[parents[key]]
            key = parents[key]
        return key

    def join(left, right):
        """Merge components only when no staff DIFFERENT verdict separates any of their members."""
        left, right = root(left), root(right)
        if left == right:
            return
        small, large = sorted((components[left], components[right]), key=len)
        if any(blocked[pk] & large for pk in small):
            return
        parents[right] = left
        components[left].update(components.pop(right))

    for item in items:
        refs = set(item.event_refs)
        refs.update(event_refs.corr_refs(item.correlation_id))
        refs.add(f"stac:{item.collection}/{item.item_id}")
        item_refs[item.pk] = refs
        for ref in sorted(refs):
            references[item.catalog_id, ref].add(item.pk)

    overrides = list(EventLinkOverride.objects.filter(catalog_id__in={i.catalog_id for i in items}))
    pairs = []
    for override in overrides:
        for left in references.get((override.catalog_id, override.disaster_ref), ()):
            for right in references.get((override.catalog_id, override.item_ref), ()):
                if left == right:
                    continue
                if override.verdict == LinkVerdict.DIFFERENT:
                    blocked[left].add(right)
                    blocked[right].add(left)
                else:
                    pairs.append((left, right))
    for left, right in pairs:
        join(left, right)
    for ids in references.values():
        representatives = []
        for pk in sorted(ids):
            for representative in representatives:
                join(representative, pk)
            if all(root(pk) != root(representative) for representative in representatives):
                representatives.append(pk)

    members = defaultdict(list)
    for item in items:
        members[root(item.pk)].append(item)
    by_item, by_ref, events = {}, defaultdict(set), {}
    identities = defaultdict(list)
    for component in members.values():
        stems = sorted(ref for i in component for ref in item_refs[i.pk] if ref.startswith("corrstem:"))
        identity = stems[0] if stems else f"item:{component[0].pk}"
        identities[component[0].catalog_id, identity].append(component)
    for (catalog_id, identity), siblings in identities.items():
        for component in siblings:
            item = next((i for i in component if "event" in i.roles), component[0])
            refs = set().union(*(item_refs[i.pk] for i in component))
            suffix = f":item:{component[0].pk}" if len(siblings) > 1 else ""
            key = f"{catalog_id}:{identity}{suffix}"
            events[key] = {
                "item": item,
                "search_text": [text for i in component for text in (i.title, i.item_id, i.correlation_id)],
            }
            for member in component:
                by_item[member.pk] = key
            for ref in refs:
                by_ref[ref].add(key)
    return events, by_item, by_ref


def event_groups(runs, *, catalog=None, search_terms=(), search_fields=()):
    """Group lightweight run identities before paging, counting each run once per event.

    One run can explicitly reference several events. The overall analysis total counts it once,
    while each event's card includes it. Full run objects are loaded only for the requested page.
    Catalog scope is applied to event identities, including reference-only runs. Search terms must
    each match either a member's metadata or the same analysis's searchable fields.
    """
    events, by_item, by_ref = _stored_events(catalog)
    groups = {}
    created_at = {}
    for run in (
        runs.prefetch_related(None)
        .order_by()
        .values("pk", "stac_item_id", "stac_item__catalog_id", "event_refs", "created_at")
        .distinct()
        .iterator(chunk_size=1000)
    ):
        created_at[run["pk"]] = run["created_at"]
        direct = by_item.get(run["stac_item_id"])
        keys = {direct} if direct else set()
        for ref in run["event_refs"]:
            for normalized in event_refs.parse(ref) or [ref]:
                matches = by_ref.get(normalized, set())
                if direct:
                    matches = {
                        key
                        for key in matches
                        if events[key]["item"].catalog_id == run["stac_item__catalog_id"]
                    }
                keys.update({direct} if direct in matches else matches)
        for key in keys:
            group = groups.setdefault(
                key, {"id": key, **events[key], "run_ids": set(), "latest_analysis": run["created_at"]}
            )
            group["run_ids"].add(run["pk"])
            group["latest_analysis"] = max(group["latest_analysis"], run["created_at"])
    for term in search_terms:
        query = Q()
        for field in search_fields:
            query |= Q(**{f"{field}__icontains": term})
        matching_runs = set(runs.filter(query).values_list("pk", flat=True))
        for group in groups.values():
            if not any(term.casefold() in text.casefold() for text in group["search_text"]):
                group["run_ids"].intersection_update(matching_runs)
    groups = {key: group for key, group in groups.items() if group["run_ids"]}
    for group in groups.values():
        group["latest_analysis"] = max(created_at[pk] for pk in group["run_ids"])
    earliest = datetime.min.replace(tzinfo=UTC)
    return sorted(
        groups.values(),
        key=lambda group: (
            -(group["item"].occurred_at or earliest).timestamp(),
            -group["latest_analysis"].timestamp(),
            group["id"],
        ),
    )


def event_cards(runs, groups):
    """Load the selected event page's runs and build a public, credential-free payload."""
    groups = list(groups)
    run_ids = set().union(*(group["run_ids"] for group in groups))
    page_runs = list(
        runs.filter(pk__in=run_ids)
        .defer("stac_item__raw", "stac_item__geom", "area__geom")
        .order_by("-created_at", "id")
    )
    cards = []
    for group in groups:
        item = group["item"]
        analyses = [run for run in page_runs if run.pk in group["run_ids"]]
        purposes = sorted({run.notebook.title if run.notebook else "Uploaded analysis" for run in analyses})
        cards.append(
            {
                "id": group["id"],
                "title": item.title or item.item_id,
                "catalog": item.catalog.slug,
                "date": item.occurred_at,
                "purposes": purposes,
                "analysis_count": len(analyses),
                "analyses": [
                    {
                        "id": run.pk,
                        "title": run.title,
                        "purpose": run.notebook.title if run.notebook else "Uploaded analysis",
                        "created_at": run.created_at,
                        "viewer_url": reverse("web:run-view", args=[run.pk]),
                    }
                    for run in analyses
                ],
            }
        )
    return cards
