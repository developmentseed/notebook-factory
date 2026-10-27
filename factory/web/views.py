from __future__ import annotations

from pathlib import Path

import markdown
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.paginator import Paginator
from django.db.models import Count, ProtectedError, Q, Value
from django.db.models.functions import Coalesce, NullIf
from django.http import HttpResponse, HttpResponseNotFound, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.safestring import mark_safe
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.http import require_POST
from django.views.static import serve

from factory.catalog import facets
from factory.catalog.models import AdminArea, Country, Facet
from factory.events.models import PollerState, StacCatalog, StacItem, TriggerRule
from factory.events.stac_client import StacClient, StacClientError
from factory.notebooks import parameters as P
from factory.notebooks.models import AnalysisNotebook, NotebookRun, RunBatch, RunStatus, TriggerKind
from factory.notebooks.parameters import ParameterError
from factory.notebooks.services import RunRequestError, cancel_run, create_batch, create_run, retry_run
from factory.notebooks.uploads import UploadError, as_archive, create_upload, inspect_archive

from . import stac_picker
from .forms import FacetForm, FacetOptionFormSet, RunDetailsForm, RunRequestForm, StacCatalogForm, UploadForm

staff_required = user_passes_test(lambda u: u.is_active and u.is_staff, login_url="web:login")


def _browse_facets(params, qs=None):
    """[{facet, options, selected}] for the filter sidebars; options limited to `qs` when given."""
    out = []
    for facet in Facet.objects.filter(show_in_browse=True).prefetch_related("options"):
        values = params.getlist(facet.key) if hasattr(params, "getlist") else []
        out.append(
            {"facet": facet, "options": list(facet.options.all()), "selected": [v for v in values if v]}
        )
    return out


def _tag_fields(facet_list, selected: dict[str, list[str]], allowed: dict[str, list] | None = None):
    """[{facet, options, selected, name}] for tag inputs (select for single, checkboxes for multiple)."""
    fields = []
    for facet in facet_list:
        options = (allowed or {}).get(facet.key) or list(facet.options.all())
        fields.append(
            {
                "facet": facet,
                "options": options,
                "selected": selected.get(facet.key, []),
                "name": f"facet_{facet.key}",
            }
        )
    return fields


def _posted_tags(post, facet_list) -> dict[str, list[str]]:
    return {f.key: [v for v in post.getlist(f"facet_{f.key}") if v] for f in facet_list}


# ---------------------------------------------------------------------------
# Browse / discover
# ---------------------------------------------------------------------------


UPLOADED = "_upload"  # the browse "template" filter value for uploaded notebooks


def _filtered_runs(request):
    qs = NotebookRun.objects.with_related()
    g = request.GET
    f = {
        "q": g.get("q", "").strip(),
        "country": g.get("country", ""),
        "template": g.get("template", ""),
        "status": g.get("status", "published"),
        "period": g.get("period", ""),
        "sort": g.get("sort", "-created_at"),
    }
    if f["q"]:
        qs = qs.filter(
            Q(notebook__title__icontains=f["q"])
            | Q(display_title__icontains=f["q"])
            | Q(summary__icontains=f["q"])
            | Q(description__icontains=f["q"])
            | Q(area__name__icontains=f["q"])
            | Q(area__country__name__icontains=f["q"])
            | Q(stac_item__title__icontains=f["q"])
            | Q(stac_item__item_id__icontains=f["q"])
            | Q(stac_item__correlation_id__icontains=f["q"])
        )
    if f["country"]:
        qs = qs.filter(
            Q(area__country_id=f["country"]) | Q(stac_item__country_codes__contains=[f["country"]])
        )
    qs = facets.filter_queryset(qs, g)
    if f["template"] == UPLOADED:
        qs = qs.filter(notebook__isnull=True)
    elif f["template"]:
        qs = qs.filter(notebook__slug=f["template"])
    if f["status"] == "published":
        qs = qs.filter(status=RunStatus.PUBLISHED)
    elif f["status"] == "active":
        qs = qs.active()
    elif f["status"] == "failed":
        qs = qs.filter(status=RunStatus.FAILED)
    if f["period"] in {"30", "90", "365"}:
        qs = qs.filter(created_at__gte=timezone.now() - timezone.timedelta(days=int(f["period"])))
    if f["sort"] == "notebook__title":
        qs = qs.order_by(Coalesce(NullIf("display_title", Value("")), "notebook__title"))
    elif f["sort"] in {"-created_at", "created_at", "area__name"}:
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
        "facet_filters": _browse_facets(request.GET),
        "templates": AnalysisNotebook.objects.filter(is_active=True),
        "uploaded": UPLOADED,
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
        .prefetch_related("facet_options__facet")
        .annotate(run_count=Count("runs"))
    )
    return render(request, "web/templates_list.html", {"templates": templates})


def template_detail(request, slug):
    nb = get_object_or_404(AnalysisNotebook.objects.prefetch_related("facet_options__facet"), slug=slug)
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
    picker_params = request.POST if request.method == "POST" else {}
    stac_item = None
    if request.GET.get("stac_item"):
        stac_item = StacItem.objects.select_related("catalog").filter(pk=request.GET["stac_item"]).first()
        if stac_item:
            initial["stac_item"] = stac_item
            picker_params = {
                "stac_catalog": str(stac_item.catalog_id),
                "stac_collection": stac_item.collection,
                "stac_item_id": stac_item.item_id,
                "stac_show_all": "1",
            }
            iso3 = (stac_item.country_codes or [None])[0]
            if iso3:
                initial["country"] = Country.objects.filter(pk=iso3).first()
    if request.GET.get("area"):
        area = AdminArea.objects.light().filter(pk=request.GET["area"]).first()
        if area:
            initial.update({"country": area.country, "level": area.level, "area_ids": str(area.pk)})
    # Tags: facets pinned by the template (exactly one option) are shown, not asked; facets
    # fed by a parameter come from that parameter. The rest can be set by hand (empty = derived).
    template_groups = {f.key: opts for f, opts in nb.facet_groups}
    fed_by_params = facets.parameter_facet_keys(nb)
    tag_facets = [
        f
        for f in Facet.objects.prefetch_related("options")
        if f.key not in fed_by_params and len(template_groups.get(f.key, [])) != 1
    ]
    pinned = [(f, opts) for f, opts in nb.facet_groups if len(opts) == 1 and f.key not in fed_by_params]
    tags = _posted_tags(request.POST, tag_facets) if request.method == "POST" else {}
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
                        stac_item=d.get("stac_item"),
                        filters=tags,
                        description=f"{d['areas'][0].country.name} · {len(d['areas'])} areas",
                    )
                    messages.success(request, f"Queued {len(d['areas'])} runs.")
                    return redirect(batch)
                run = create_run(
                    nb,
                    params,
                    area=d["areas"][0] if d["areas"] else None,
                    stac_item=d.get("stac_item"),
                    filters=tags,
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
        "stac_item": stac_item,
        "picker": stac_picker.build(nb, picker_params) if nb.requires_stac_item else None,
        "selected_area_ids": request.POST.get("area_ids", initial.get("area_ids", "")),
        "tag_fields": _tag_fields(tag_facets, tags, allowed=template_groups),
        "pinned_tags": pinned,
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
    ctx["tags"] = _run_tags_ctx(request, run)
    ctx["can_edit"] = _can_edit_tags(request.user, run)
    return render(request, "web/run_detail.html", ctx)


def _can_edit_tags(user, run) -> bool:
    return user.is_authenticated and (user.is_staff or run.triggered_by_id == user.pk)


def _run_tags_ctx(request, run, editing=False):
    all_facets = list(Facet.objects.prefetch_related("options"))
    return {
        "run": run,
        "can_edit": _can_edit_tags(request.user, run),
        "editing": editing,
        "tag_fields": _tag_fields(all_facets, facets.manual_tags(run)),
    }


@login_required
def run_tags(request, pk):
    """Show / edit a run's manual tags (HTMX partial). Clearing a filter falls back to derived tags."""
    run = get_object_or_404(NotebookRun.objects.with_related(), pk=pk)
    if not _can_edit_tags(request.user, run):
        return HttpResponse("Only the requester or staff can edit tags.", status=403)
    if request.method == "POST":
        all_facets = list(Facet.objects.all())
        facets.update_run_manual(run, _posted_tags(request.POST, all_facets))
        run = NotebookRun.objects.with_related().get(pk=pk)
        run.log(f"tags edited by {request.user.get_username()}")
        return render(request, "web/partials/run_tags.html", _run_tags_ctx(request, run))
    return render(request, "web/partials/run_tags.html", _run_tags_ctx(request, run, editing=True))


@login_required
def run_details(request, pk):
    """Show / edit a run's title, summary and description (HTMX partial)."""
    run = get_object_or_404(NotebookRun.objects.with_related(), pk=pk)
    if not _can_edit_tags(request.user, run):
        return HttpResponse("Only the requester or staff can edit details.", status=403)
    form = RunDetailsForm(request.POST or None, instance=run)
    if request.method == "POST" and form.is_valid():
        form.save()
        run.log(f"details edited by {request.user.get_username()}")
        return render(request, "web/partials/run_details.html", {"run": run, "can_edit": True})
    return render(
        request,
        "web/partials/run_details.html",
        {"run": run, "can_edit": True, "editing": True, "form": form},
    )


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
# Uploads (already executed notebooks)
# ---------------------------------------------------------------------------


@staff_required
def upload_new(request):
    all_facets = list(Facet.objects.prefetch_related("options"))
    tags = _posted_tags(request.POST, all_facets) if request.method == "POST" else {}
    form = UploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            run = create_upload(
                d["archive"],
                notebook_path=form.manifest.notebook,
                title=d["title"],
                summary=d["summary"],
                description=d["description"],
                area=d["area"],
                stac_item=d["stac_item"],
                filters=tags,
                user=request.user,
            )
        except UploadError as exc:
            form.add_error("file", str(exc))
        else:
            messages.success(request, "Notebook uploaded; it is being rendered.")
            return redirect(run)
    ctx = {
        "form": form,
        "detail_fields": [form["title"], form["summary"], form["description"]],
        "manifest": form.manifest,
        "countries": Country.objects.filter(areas__isnull=False).distinct().order_by("name"),
        "levels": sorted(set(AdminArea.objects.values_list("level", flat=True).distinct())),
        "selected_area_ids": request.POST.get("area_ids", ""),
        "tag_fields": _tag_fields(all_facets, tags),
        "stac_open": bool(request.POST.get("stac_item_id")),
        "picker": stac_picker.build(None, request.POST) if request.POST.get("stac_item_id") else None,
    }
    return render(request, "web/upload_new.html", ctx)


@staff_required
@require_POST
def upload_inspect(request):
    """Describe the chosen file as soon as it is picked: its notebooks, title and warnings (HTMX)."""
    upload = request.FILES.get("file")
    ctx = {"manifest": None, "error": "", "selected": request.POST.get("notebook_path", "")}
    if upload is not None:
        try:
            ctx["manifest"] = inspect_archive(as_archive(upload, upload.name), ctx["selected"])
        except UploadError as exc:
            ctx["error"] = str(exc)
    return render(request, "web/partials/upload_inspect.html", ctx)


@staff_required
def upload_stac_picker(request):
    return render(request, "web/partials/stac_picker.html", {"picker": stac_picker.build(None, request.GET)})


# ---------------------------------------------------------------------------
# STAC picker (run form)
# ---------------------------------------------------------------------------


@login_required
def stac_picker_partial(request, slug):
    """Re-render the catalog → collection → item picker from its own inputs (HTMX)."""
    nb = get_object_or_404(AnalysisNotebook, slug=slug, is_active=True)
    return render(request, "web/partials/stac_picker.html", {"picker": stac_picker.build(nb, request.GET)})


# ---------------------------------------------------------------------------
# STAC items
# ---------------------------------------------------------------------------


def stac_item_list(request):
    qs = (
        StacItem.objects.select_related("catalog")
        .prefetch_related("facet_options__facet")
        .annotate(run_count=Count("runs", distinct=True))
    )
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(
            Q(title__icontains=q)
            | Q(item_id__icontains=q)
            | Q(correlation_id__icontains=q)
            | Q(country_codes__contains=[q.upper()])
        )
    if request.GET.get("catalog"):
        qs = qs.filter(catalog__slug=request.GET["catalog"])
    qs = facets.filter_queryset(qs, request.GET)
    page = Paginator(qs, 40).get_page(request.GET.get("page"))
    return render(
        request,
        "web/stac_items_list.html",
        {
            "page": page,
            "q": q,
            "catalogs": StacCatalog.objects.all(),
            "facet_filters": [f for f in _browse_facets(request.GET) if f["facet"].stac_property],
            "rules": TriggerRule.objects.select_related("notebook", "catalog"),
            "poller": PollerState.objects.select_related("catalog"),
        },
    )


def stac_item_detail(request, pk):
    item = get_object_or_404(
        StacItem.objects.select_related("catalog").prefetch_related("facet_options__facet"), pk=pk
    )
    runs = item.runs.with_related()
    templates = AnalysisNotebook.objects.filter(is_active=True, requires_stac_item=True)
    return render(request, "web/stac_item_detail.html", {"item": item, "runs": runs, "templates": templates})


# ---------------------------------------------------------------------------
# STAC catalogs (staff)
# ---------------------------------------------------------------------------


@staff_required
def catalog_list(request):
    catalogs = StacCatalog.objects.annotate(item_count=Count("items", distinct=True))
    return render(request, "web/catalogs_list.html", {"catalogs": catalogs})


@staff_required
def catalog_edit(request, slug=None):
    catalog = get_object_or_404(StacCatalog, slug=slug) if slug else None
    form = StacCatalogForm(request.POST or None, instance=catalog)
    if request.method == "POST" and form.is_valid():
        catalog = form.save()
        messages.success(request, f"Saved catalog {catalog}.")
        return redirect("web:catalog-list")
    return render(request, "web/catalog_form.html", {"form": form, "catalog": catalog})


@staff_required
@require_POST
def catalog_delete(request, slug):
    catalog = get_object_or_404(StacCatalog, slug=slug)
    try:
        catalog.delete()
        messages.info(request, f"Deleted catalog {catalog}.")
    except ProtectedError:
        messages.error(request, f"{catalog} has stored STAC items; deactivate it instead of deleting it.")
    return redirect("web:catalog-list")


@staff_required
@require_POST
def catalog_test(request):
    """Try the URL / key currently in the form (falls back to the stored key when the field is blank)."""
    url = request.POST.get("url", "").strip()
    api_key = request.POST.get("api_key", "").strip()
    slug = request.POST.get("existing_slug", "")
    if not api_key and slug and not request.POST.get("clear_api_key"):
        stored = StacCatalog.objects.filter(slug=slug).first()
        api_key = stored.api_key if stored else ""
    ctx = {"ok": False, "message": "", "collections": []}
    if not url:
        ctx["message"] = "Enter the STAC API URL first."
    else:
        try:
            with StacClient(url, api_key) as client:
                root = client.root()
                collections = client.collections()
            ctx.update(
                ok=True,
                message=f"{root.get('title') or root.get('id') or url} · {len(collections)} collections",
                collections=[c.get("id") for c in collections[:12]],
            )
        except StacClientError as exc:
            ctx["message"] = str(exc)
    return render(request, "web/partials/catalog_test.html", ctx)


# ---------------------------------------------------------------------------
# Filters (staff)
# ---------------------------------------------------------------------------


def _facet_usage(facet) -> int:
    from factory.notebooks.models import RunFacet

    options = facet.options.all()
    return (
        RunFacet.objects.filter(option__in=options).count()
        + AnalysisNotebook.objects.filter(facet_options__in=options).count()
        + StacItem.objects.filter(facet_options__in=options).count()
        + TriggerRule.objects.filter(facet_options__in=options).count()
    )


@staff_required
def facet_list(request):
    facet_rows = Facet.objects.prefetch_related("options").annotate(
        run_count=Count("options__run_facets__run", distinct=True)
    )
    return render(request, "web/facets_list.html", {"facets": facet_rows})


@staff_required
def facet_edit(request, key=None):
    facet = get_object_or_404(Facet, key=key) if key else None
    form = FacetForm(request.POST or None, instance=facet)
    formset = FacetOptionFormSet(request.POST or None, instance=facet or Facet(), prefix="options")
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        facet = form.save()
        formset.instance = facet
        formset.save()
        messages.success(request, f"Saved filter {facet}.")
        return redirect("web:facet-list")
    return render(request, "web/facet_form.html", {"form": form, "formset": formset, "facet": facet})


@staff_required
@require_POST
def facet_delete(request, key):
    facet = get_object_or_404(Facet, key=key)
    used = _facet_usage(facet)
    if used:
        messages.error(
            request, f"{facet} is used {used} time(s) by runs, templates, items or rules; not deleted."
        )
    else:
        facet.delete()
        messages.info(request, f"Deleted filter {facet}.")
    return redirect("web:facet-list")


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


HELP_GUIDE = Path(__file__).parent / "help" / "guide.md"


def help_page(request):
    """The user guide: help/guide.md rendered with a table of contents."""
    text = HELP_GUIDE.read_text(encoding="utf-8")
    text = text.replace("{{BASE_URL}}", request.build_absolute_uri("/")).replace(
        "{{SITE_NAME}}", settings.SITE_NAME
    )
    converter = markdown.Markdown(
        extensions=["tables", "fenced_code", "toc"], extension_configs={"toc": {"toc_depth": "2-3"}}
    )
    body = converter.convert(text)
    return render(request, "web/help.html", {"body": mark_safe(body), "toc": mark_safe(converter.toc)})


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
