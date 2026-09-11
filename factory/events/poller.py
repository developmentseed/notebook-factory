"""
Scheduled poll of Montandon: find new events, match rules, enqueue runs.

Webhooks from Montandon would replace this later; the matching / run creation
is shared so that swap is small.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from factory.catalog.models import AdminArea
from factory.notebooks.models import TriggerKind
from factory.notebooks.services import RunRequestError, create_batch, create_run

from .matching import render_parameters, rule_matches, upsert_event
from .models import Event, EventRunLink, EventTriggerRule, PollerState
from .montandon import MontandonClient, MontandonError

log = logging.getLogger(__name__)


def poll(client: MontandonClient | None = None, dry_run: bool = False) -> dict:
    """Poll every collection referenced by an enabled rule. Returns a summary dict."""
    cfg = settings.MONTANDON
    rules = list(
        EventTriggerRule.objects.filter(enabled=True).prefetch_related("hazards").select_related("notebook")
    )
    collections = sorted({c for r in rules for c in (r.collections or [])})
    summary = {"collections": {}, "events_new": 0, "events_updated": 0, "runs_created": 0, "errors": []}
    if not collections:
        log.info("poll: no enabled trigger rules with collections")
        return summary
    client = client or MontandonClient()
    now = timezone.now()
    for collection in collections:
        state, _ = PollerState.objects.get_or_create(collection=collection)
        if state.last_item_datetime:
            since = state.last_item_datetime - timedelta(minutes=cfg["POLL_OVERLAP_MINUTES"])
        else:
            since = now - timedelta(days=cfg["POLL_LOOKBACK_DAYS"])
        seen = 0
        newest = state.last_item_datetime
        try:
            for item in client.search([collection], since=since, limit=cfg["POLL_PAGE_SIZE"]):
                item.setdefault("collection", collection)
                seen += 1
                if dry_run:
                    continue
                event, created, changed = upsert_event(item)
                summary["events_new"] += created
                summary["events_updated"] += not created and changed
                if event.occurred_at and (newest is None or event.occurred_at > newest):
                    newest = event.occurred_at
                summary["runs_created"] += fire_rules(event, rules, created=created, changed=changed)
            state.last_error = ""
        except MontandonError as exc:
            log.warning("poll %s failed: %s", collection, exc)
            state.last_error = str(exc)
            summary["errors"].append(f"{collection}: {exc}")
        state.last_polled_at = now
        state.items_seen += seen
        if newest:
            state.last_item_datetime = newest
        state.save()
        summary["collections"][collection] = seen
    return summary


def fire_rules(
    event: Event, rules: list[EventTriggerRule] | None = None, *, created: bool = True, changed: bool = True
) -> int:
    """Create runs for every rule the event matches. Returns the number of runs created."""
    rules = (
        rules
        if rules is not None
        else list(EventTriggerRule.objects.filter(enabled=True).prefetch_related("hazards"))
    )
    n = 0
    for rule in rules:
        if not rule_matches(rule, event):
            continue
        link = EventRunLink.objects.filter(event=event, rule=rule).first()
        if link is not None:
            if not (rule.rerun_on_update and changed and link.item_updated_at != event.item_updated_at):
                continue
        try:
            n += _create_runs_for_rule(rule, event)
        except RunRequestError as exc:
            log.warning("rule %s could not run for event %s: %s", rule, event, exc)
            continue
        EventRunLink.objects.update_or_create(
            event=event, rule=rule, defaults={"item_updated_at": event.item_updated_at}
        )
        rule.last_triggered_at = timezone.now()
        rule.save(update_fields=["last_triggered_at"])
    return n


def _create_runs_for_rule(rule: EventTriggerRule, event: Event) -> int:
    notebook = rule.notebook
    countries = event.country_codes or []
    if notebook.requires_area:
        if rule.area_level:
            areas = list(AdminArea.objects.filter(country_id__in=countries, level=rule.area_level).light())
            if not areas:
                raise RunRequestError(f"no admin-{rule.area_level} areas loaded for {countries}")
            params = render_parameters(rule.parameter_template, event)
            create_batch(
                notebook,
                params,
                areas,
                description=f"{event.title} · {len(areas)} areas",
                trigger=TriggerKind.AUTO,
                notify_emails=rule.notify_emails,
                event=event,
                trigger_rule=rule,
            )
            return len(areas)
        areas = list(AdminArea.objects.filter(country_id__in=countries, level=0).light())
        if not areas:
            raise RunRequestError(f"no country boundaries loaded for {countries}")
        for area in areas:
            params = render_parameters(rule.parameter_template, event, area)
            create_run(
                notebook,
                params,
                area=area,
                event=event,
                trigger=TriggerKind.AUTO,
                trigger_rule=rule,
                notify_emails=rule.notify_emails,
            )
        return len(areas)
    params = render_parameters(rule.parameter_template, event)
    create_run(
        notebook,
        params,
        event=event,
        trigger=TriggerKind.AUTO,
        trigger_rule=rule,
        notify_emails=rule.notify_emails,
    )
    return 1
