from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import HttpResponse, HttpResponseNotFound, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.http import require_POST
from django.views.static import serve

from factory.catalog.models import AdminArea, Country, Hazard
from factory.events.models import Event, EventTriggerRule, PollerState
from factory.notebooks import parameters as P
from factory.notebooks.models import AnalysisNotebook, NotebookRun, RunBatch, RunStatus, TriggerKind, UseCase
from factory.notebooks.parameters import ParameterError
from factory.notebooks.services import RunRequestError, cancel_run, create_batch, create_run, retry_run

from .forms import RunRequestForm

# ---------------------------------------------------------------------------
# Browse / discover
# ---------------------------------------------------------------------------


def _filtered_runs(request):
    qs = NotebookRun.objects.with_related()
    g = request.GET
    f = {
        "q": g.get("q", "").strip(),
        "country": g.get("country", ""),
        "hazard": g.get("hazard", ""),
        "use_case": g.get("use_case", ""),
        "template": g.get("template", ""),
        "status": g.get("status", "published"),
        "period": g.get("period", ""),
        "sort": g.get("sort", "-created_at"),
    }
    if f["q"]:
        qs = qs.filter(
            Q(notebook__title__icontains=f["q"])
            | Q(area__name__icontains=f["q"])
            | Q(area__country__name__icontains=f["q"])
            | Q(event__title__icontains=f["q"])
            | Q(event__monty_corr_id__icontains=f["q"])
        )
    if f["country"]:
        qs = qs.filter(Q(area__country_id=f["country"]) | Q(event__country_codes__contains=[f["country"]]))
    if f["hazard"]:
        qs = qs.filter(hazard__key=f["hazard"])
    if f["use_case"]:
        qs = qs.filter(notebook__use_case=f["use_case"])
    if f["template"]:
        qs = qs.filter(notebook__slug=f["template"])
    if f["status"] == "published":
        qs = qs.filter(status=RunStatus.PUBLISHED)
    elif f["status"] == "active":
        qs = qs.active()
    elif f["status"] == "failed":
        qs = qs.filter(status=RunStatus.FAILED)
    if f["period"] in {"30", "90", "365"}:
        qs = qs.filter(created_at__gte=timezone.now() - timezone.timedelta(days=int(f["period"])))
    if f["sort"] in {"-created_at", "created_at", "notebook__title", "area__name"}:
        qs = qs.order_by(f["sort"])
    return qs, f


def browse(request):
    qs, f = _filtered_runs(request)
    paginator = Paginator(qs, 24)
    page = paginator.get_page(request.GET.get("page"))
    ctx = {
        "page": page,
        "filters": f,
        "countries": Country.objects.filter(Q(areas__runs__isnull=False)).distinct().order_by("name"),
        "hazards": Hazard.objects.all(),
        "use_cases": UseCase.choices,
        "templates": AnalysisNotebook.objects.filter(is_active=True),
        "total": paginator.count,
    }
    if request.htmx:
        return render(request, "web/partials/run_cards.html", ctx)
    return render(request, "web/browse.html", ctx)


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


def template_list(request):
    templates = (
        AnalysisNotebook.objects.filter(is_active=True)
        .prefetch_related("hazards")
        .annotate(run_count=Count("runs"))
    )
    return render(request, "web/templates_list.html", {"templates": templates})


def template_detail(request, slug):
    nb = get_object_or_404(AnalysisNotebook.objects.prefetch_related("hazards"), slug=slug)
    runs = nb.runs.with_related()[:10]
    return render(
        request,
        "web/template_detail.html",
        {
            "nb": nb,
            "fields": P.schema_fields(nb.parameter_schema),
            "runs": runs,
            "run_count": nb.runs.count(),
            "rules": nb.trigger_rules.all(),
        },
    )


@login_required
def run_new(request, slug):
    nb = get_object_or_404(AnalysisNotebook, slug=slug, is_active=True)
    initial = {}
    event = None
    if request.GET.get("event"):
        event = Event.objects.filter(pk=request.GET["event"]).first()
        if event:
            initial["event"] = event
            iso3 = (event.country_codes or [None])[0]
            if iso3:
                initial["country"] = Country.objects.filter(pk=iso3).first()
    if request.GET.get("area"):
        area = AdminArea.objects.light().filter(pk=request.GET["area"]).first()
        if area:
            initial.update({"country": area.country, "level": area.level, "area_ids": str(area.pk)})
    if request.method == "POST":
        form = RunRequestForm(nb, request.POST)
        params = P.coerce_form_data(nb.parameter_schema, request.POST)
        param_errors = {}
        try:
            params = P.validate(nb.parameter_schema, params)
        except ParameterError as exc:
            param_errors = exc.errors
        if form.is_valid() and not param_errors:
            d = form.cleaned_data
            try:
                if len(d["areas"]) > 1:
                    batch = create_batch(
                        nb,
                        params,
                        d["areas"],
                        user=request.user,
                        trigger=TriggerKind.MANUAL,
                        notify_emails=d["notify_emails"],
                        event=d.get("event"),
                        description=f"{d['areas'][0].country.name} · {len(d['areas'])} areas",
                    )
                    messages.success(request, f"Queued {len(d['areas'])} runs.")
                    return redirect(batch)
                run = create_run(
                    nb,
                    params,
                    area=d["areas"][0] if d["areas"] else None,
                    event=d.get("event"),
                    user=request.user,
                    trigger=TriggerKind.MANUAL,
                    notify_emails=d["notify_emails"],
                )
                messages.success(request, "Run queued.")
                return redirect(run)
            except RunRequestError as exc:
                form.add_error(None, str(exc))
    else:
        form = RunRequestForm(nb, initial=initial)
        params = P.defaults(nb.parameter_schema)
        param_errors = {}
    ctx = {
        "nb": nb,
        "form": form,
        "fields": P.schema_fields(nb.parameter_schema),
        "values": params,
        "param_errors": param_errors,
        "countries": Country.objects.filter(areas__isnull=False).distinct().order_by("name"),
        "levels": sorted(set(AdminArea.objects.values_list("level", flat=True).distinct())),
        "event": event,
        "recent_events": Event.objects.select_related("hazard")[:50] if nb.requires_event else [],
        "selected_area_ids": request.POST.get("area_ids", initial.get("area_ids", "")),
    }
    return render(request, "web/run_new.html", ctx)


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


def run_detail(request, pk):
    run = get_object_or_404(NotebookRun.objects.with_related(), pk=pk)
    ctx = {"run": run, "log": run.log_entries.all()[:400], "notifications": run.notifications.all()}
    if request.htmx:
        return render(request, "web/partials/run_status.html", ctx)
    return render(request, "web/run_detail.html", ctx)


def run_status_json(request, pk):
    run = get_object_or_404(NotebookRun, pk=pk)
    return JsonResponse(
        {
            "id": str(run.id),
            "status": run.status,
            "progress": run.progress,
            "output_url": run.output_url,
            "error": run.error,
        }
    )


def run_view(request, pk):
    run = get_object_or_404(NotebookRun.objects.with_related(), pk=pk)
    if run.status != RunStatus.PUBLISHED or not run.output_url:
        return redirect(run)
    return render(request, "web/run_view.html", {"run": run})


@login_required
@require_POST
def run_cancel(request, pk):
    run = get_object_or_404(NotebookRun, pk=pk)
    if cancel_run(run):
        messages.info(request, "Run cancelled.")
    return redirect(run)


@login_required
@require_POST
def run_retry(request, pk):
    run = get_object_or_404(NotebookRun.objects.with_related(), pk=pk)
    new = retry_run(run, user=request.user)
    messages.success(request, "New run queued.")
    return redirect(new)


def batch_detail(request, pk):
    batch = get_object_or_404(RunBatch.objects.select_related("notebook"), pk=pk)
    runs = batch.runs.with_related().order_by("area__name")
    ctx = {"batch": batch, "runs": runs, "counts": batch.status_counts(), "total": runs.count()}
    if request.htmx:
        return render(request, "web/partials/batch_runs.html", ctx)
    return render(request, "web/batch_detail.html", ctx)


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


def event_list(request):
    qs = Event.objects.select_related("hazard").annotate(run_count=Count("runs"))
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(
            Q(title__icontains=q) | Q(monty_corr_id__icontains=q) | Q(country_codes__contains=[q.upper()])
        )
    if request.GET.get("hazard"):
        qs = qs.filter(hazard__key=request.GET["hazard"])
    page = Paginator(qs, 40).get_page(request.GET.get("page"))
    return render(
        request,
        "web/events_list.html",
        {
            "page": page,
            "q": q,
            "hazards": Hazard.objects.all(),
            "rules": EventTriggerRule.objects.select_related("notebook"),
            "poller": PollerState.objects.all(),
        },
    )


def event_detail(request, pk):
    event = get_object_or_404(Event.objects.select_related("hazard"), pk=pk)
    runs = event.runs.with_related()
    templates = AnalysisNotebook.objects.filter(is_active=True, requires_event=True)
    return render(request, "web/event_detail.html", {"event": event, "runs": runs, "templates": templates})


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------


@login_required
def notification_list(request):
    qs = request.user.notifications.select_related("run", "run__notebook")
    if request.method == "POST":
        qs.filter(read_at__isnull=True).update(read_at=timezone.now())
        return redirect("web:notifications")
    return render(request, "web/notifications.html", {"notifications": qs[:100]})


@login_required
def notification_open(request, pk):
    n = get_object_or_404(request.user.notifications, pk=pk)
    if not n.read_at:
        n.read_at = timezone.now()
        n.save(update_fields=["read_at"])
    return redirect(n.run)


def healthz(request):
    from django.db import connection

    with connection.cursor() as c:
        c.execute("SELECT 1")
    return HttpResponse("ok", content_type="text/plain")


# ---------------------------------------------------------------------------
# Dev-only: serve locally "published" notebooks like object storage would
# ---------------------------------------------------------------------------


@xframe_options_exempt
def published_file(request, path):
    """
    Serve files from PUBLISHED_ROOT. Directory paths resolve to index.html (the MyST
    app navigates to its base path on load), and no response — 404s included — carries
    X-Frame-Options, so the result viewer can embed the pages just like S3 / Azure would.
    """
    root = Path(settings.PUBLISHED_ROOT).resolve()
    target = (root / path).resolve()
    if root != target and root not in target.parents:
        return HttpResponseNotFound("Not found")
    if target.is_dir():
        target = target / "index.html"
    if not target.is_file():
        return HttpResponseNotFound("Not found")
    return serve(request, str(target.relative_to(root)), document_root=str(root))
