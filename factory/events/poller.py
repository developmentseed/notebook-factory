"""
Scheduled poll of STAC catalogs: find new items, match rules, enqueue runs.

Webhooks would replace this later; the matching / run creation is shared so
that swap is small.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from factory.catalog.models import AdminArea
from factory.notebooks.models import TriggerKind
from factory.notebooks.services import RunRequestError, create_batch, create_run

from .matching import render_parameters, rule_matches, upsert_item
from .models import ItemRuleLink, PollerState, StacCatalog, StacItem, TriggerRule
from .stac_client import StacClient, StacClientError

log = logging.getLogger(__name__)


def _watch_list(rules: list[TriggerRule]) -> dict[StacCatalog, dict[str, dict]]:
    """{catalog: {collection: merged search filter}} for every enabled rule.

    Rules sharing a (catalog, collection) but with different filters are polled
    without a filter; rule_matches() still decides which of them fire.
    """
    out: dict[StacCatalog, dict[str, dict]] = defaultdict(dict)
    for rule in rules:
        for collection in rule.collections or []:
            current = out[rule.catalog].get(collection)
            flt = rule.search_filter or {}
            if current is None:
                out[rule.catalog][collection] = flt
            elif current != flt:
                out[rule.catalog][collection] = {}
    return out


def poll(clients: dict[int, StacClient] | None = None, dry_run: bool = False) -> dict:
    """Poll every (catalog, collection) referenced by an enabled rule. Returns a summary dict.

    `clients` maps catalog pk -> client and is only used by tests.
    """
    cfg = settings.STAC
    rules = list(
        TriggerRule.objects.filter(enabled=True, catalog__is_active=True)
        .prefetch_related("hazards")
        .select_related("notebook", "catalog")
    )
    summary = {"collections": {}, "items_new": 0, "items_updated": 0, "runs_created": 0, "errors": []}
    watch = _watch_list(rules)
    if not watch:
        log.info("poll: no enabled trigger rules with collections")
        return summary
    now = timezone.now()
    for catalog, collections in watch.items():
        client = (clients or {}).get(catalog.pk) or catalog.client()
        catalog_errors = []
        for collection, search_filter in sorted(collections.items()):
            key = f"{catalog.slug}:{collection}"
            state, _ = PollerState.objects.get_or_create(catalog=catalog, collection=collection)
            if state.last_item_datetime:
                since = state.last_item_datetime - timedelta(minutes=cfg["POLL_OVERLAP_MINUTES"])
            else:
                since = now - timedelta(days=cfg["POLL_LOOKBACK_DAYS"])
            seen = 0
            newest = state.last_item_datetime
            try:
                for raw in client.search(
                    [collection], since=since, filter=search_filter or None, limit=cfg["POLL_PAGE_SIZE"]
                ):
                    seen += 1
                    if dry_run:
                        continue
                    item, created, changed = upsert_item(catalog, raw, collection=collection)
                    summary["items_new"] += created
                    summary["items_updated"] += not created and changed
                    if item.occurred_at and (newest is None or item.occurred_at > newest):
                        newest = item.occurred_at
                    summary["runs_created"] += fire_rules(item, rules, created=created, changed=changed)
                state.last_error = ""
            except StacClientError as exc:
                log.warning("poll %s failed: %s", key, exc)
                state.last_error = str(exc)
                catalog_errors.append(f"{collection}: {exc}")
                summary["errors"].append(f"{key}: {exc}")
            state.last_polled_at = now
            state.items_seen += seen
            if newest:
                state.last_item_datetime = newest
            state.save()
            summary["collections"][key] = seen
        catalog.last_checked_at = now
        catalog.last_error = "\n".join(catalog_errors)
        catalog.save(update_fields=["last_checked_at", "last_error", "updated_at"])
    return summary


def fire_rules(
    item: StacItem, rules: list[TriggerRule] | None = None, *, created: bool = True, changed: bool = True
) -> int:
    """Create runs for every rule the item matches. Returns the number of runs created."""
    rules = (
        rules
        if rules is not None
        else list(TriggerRule.objects.filter(enabled=True, catalog=item.catalog).prefetch_related("hazards"))
    )
    n = 0
    for rule in rules:
        if not rule_matches(rule, item):
            continue
        link = ItemRuleLink.objects.filter(item=item, rule=rule).first()
        if link is not None:
            if not (rule.rerun_on_update and changed and link.item_updated_at != item.item_updated_at):
                continue
        try:
            n += _create_runs_for_rule(rule, item)
        except RunRequestError as exc:
            log.warning("rule %s could not run for item %s: %s", rule, item, exc)
            continue
        ItemRuleLink.objects.update_or_create(
            item=item, rule=rule, defaults={"item_updated_at": item.item_updated_at}
        )
        rule.last_triggered_at = timezone.now()
        rule.save(update_fields=["last_triggered_at"])
    return n


def _create_runs_for_rule(rule: TriggerRule, item: StacItem) -> int:
    notebook = rule.notebook
    countries = item.country_codes or []
    if notebook.requires_area:
        if not countries:
            raise RunRequestError("template needs an area but the item has no country codes")
        if rule.area_level:
            areas = list(AdminArea.objects.filter(country_id__in=countries, level=rule.area_level).light())
            if not areas:
                raise RunRequestError(f"no admin-{rule.area_level} areas loaded for {countries}")
            params = render_parameters(rule.parameter_template, item)
            create_batch(
                notebook,
                params,
                areas,
                description=f"{item} · {len(areas)} areas",
                trigger=TriggerKind.AUTO,
                notify_emails=rule.notify_emails,
                stac_item=item,
                trigger_rule=rule,
            )
            return len(areas)
        areas = list(AdminArea.objects.filter(country_id__in=countries, level=0).light())
        if not areas:
            raise RunRequestError(f"no country boundaries loaded for {countries}")
        for area in areas:
            params = render_parameters(rule.parameter_template, item, area)
            create_run(
                notebook,
                params,
                area=area,
                stac_item=item,
                trigger=TriggerKind.AUTO,
                trigger_rule=rule,
                notify_emails=rule.notify_emails,
            )
        return len(areas)
    params = render_parameters(rule.parameter_template, item)
    create_run(
        notebook,
        params,
        stac_item=item,
        trigger=TriggerKind.AUTO,
        trigger_rule=rule,
        notify_emails=rule.notify_emails,
    )
    return 1
