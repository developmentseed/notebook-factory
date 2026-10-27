from django.conf import settings
from django.db.models import Count
from django.http import JsonResponse
from django.views.decorators.cache import cache_page
from django_filters import rest_framework as filters
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import (
    SAFE_METHODS,
    BasePermission,
    IsAdminUser,
    IsAuthenticated,
    IsAuthenticatedOrReadOnly,
)
from rest_framework.response import Response

from factory.catalog import facets
from factory.catalog.models import AdminArea, Country, Facet, FacetOption
from factory.events.models import StacCatalog, StacItem, TriggerRule
from factory.events.stac_client import StacClientError
from factory.notebooks.models import AnalysisNotebook, NotebookRun, RunBatch, TriggerKind
from factory.notebooks.parameters import ParameterError
from factory.notebooks.services import RunRequestError, cancel_run, create_batch, create_run, retry_run
from factory.notebooks.uploads import UploadError, create_upload

from . import serializers as S


class ReadAnyWriteStaff(BasePermission):
    def has_permission(self, request, view):
        return request.method in SAFE_METHODS or bool(request.user and request.user.is_staff)


class FacetFilterMixin:
    """Apply ?<facet_key>=<option> query params (any option within a filter, all filters together)."""

    facet_relation = "facet_options"

    def filter_queryset(self, queryset):
        queryset = super().filter_queryset(queryset)
        return facets.filter_queryset(queryset, self.request.query_params, rel=self.facet_relation)


class FacetViewSet(viewsets.ModelViewSet):
    """Filters (e.g. hazard, use case) with their options. Anyone can read; staff can edit."""

    queryset = Facet.objects.prefetch_related("options")
    serializer_class = S.FacetSerializer
    permission_classes = [ReadAnyWriteStaff]
    lookup_field = "key"
    pagination_class = None


class FacetOptionViewSet(viewsets.ModelViewSet):
    queryset = FacetOption.objects.select_related("facet")
    serializer_class = S.FacetOptionSerializer
    permission_classes = [ReadAnyWriteStaff]
    filterset_fields = ["facet__key"]
    pagination_class = None


class CountryViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Country.objects.annotate(area_count=Count("areas")).filter(area_count__gt=0)
    serializer_class = S.CountrySerializer
    pagination_class = None
    search_fields = ["name", "iso3"]


class AdminAreaFilter(filters.FilterSet):
    country = filters.CharFilter(method="filter_country")
    level = filters.NumberFilter()
    parent = filters.NumberFilter(field_name="parent_id")
    source = filters.CharFilter()

    class Meta:
        model = AdminArea
        fields = ["country", "level", "parent", "source"]

    def filter_country(self, qs, name, value):
        return qs.filter(country_id=value.upper())


class AdminAreaViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = AdminArea.objects.light().select_related("country")
    serializer_class = S.AdminAreaSerializer
    filterset_class = AdminAreaFilter
    search_fields = ["name", "code"]
    ordering_fields = ["name", "level"]

    @action(detail=False, url_path="geojson")
    def geojson(self, request):
        """GeoJSON FeatureCollection with simplified geometries for the map picker."""
        qs = self.filter_queryset(AdminArea.objects.select_related("country").exclude(geom=None))
        if not any(request.query_params.get(k) for k in ("country", "parent", "level")):
            return Response({"detail": "Filter by country, level or parent."}, status=400)
        tol = settings.NOTEBOOK_FACTORY["AREA_SIMPLIFY_TOLERANCE"]
        try:
            tol = float(request.query_params.get("simplify", tol))
        except ValueError:
            pass
        features = [a.as_feature(simplify=tol or None) for a in qs[:5000]]
        return JsonResponse({"type": "FeatureCollection", "features": features})

    @action(detail=True, url_path="geojson")
    def feature(self, request, pk=None):
        area = AdminArea.objects.select_related("country").get(pk=pk)
        return JsonResponse(area.as_feature())


class AnalysisNotebookViewSet(FacetFilterMixin, viewsets.ReadOnlyModelViewSet):
    queryset = (
        AnalysisNotebook.objects.filter(is_active=True)
        .prefetch_related("facet_options__facet")
        .annotate(run_count=Count("runs", distinct=True))
    )
    serializer_class = S.AnalysisNotebookSerializer
    lookup_field = "slug"
    filterset_fields = ["requires_stac_item", "requires_area"]
    search_fields = ["title", "summary", "description"]


class RunFilter(filters.FilterSet):
    notebook = filters.CharFilter(field_name="notebook__slug")
    country = filters.CharFilter(method="filter_country")
    area = filters.NumberFilter(field_name="area_id")
    stac_item = filters.NumberFilter(field_name="stac_item_id")
    catalog = filters.CharFilter(field_name="stac_item__catalog__slug")
    status = filters.CharFilter()
    trigger = filters.CharFilter()
    batch = filters.UUIDFilter(field_name="batch_id")
    since = filters.IsoDateTimeFilter(field_name="created_at", lookup_expr="gte")
    until = filters.IsoDateTimeFilter(field_name="created_at", lookup_expr="lte")

    class Meta:
        model = NotebookRun
        fields = []

    def filter_country(self, qs, name, value):
        value = value.upper()
        from django.db.models import Q

        return qs.filter(Q(area__country_id=value) | Q(stac_item__country_codes__contains=[value]))


class NotebookRunViewSet(
    FacetFilterMixin,
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    queryset = NotebookRun.objects.with_related()
    filterset_class = RunFilter
    search_fields = [
        "notebook__title",
        "display_title",
        "summary",
        "description",
        "area__name",
        "stac_item__title",
        "stac_item__item_id",
        "stac_item__correlation_id",
    ]
    ordering_fields = ["created_at", "finished_at", "status"]
    permission_classes = [IsAuthenticatedOrReadOnly]

    def get_serializer_class(self):
        if self.action == "create":
            return S.RunCreateSerializer
        if self.action == "upload":
            return S.UploadCreateSerializer
        if self.action == "retrieve":
            return S.NotebookRunDetailSerializer
        return S.NotebookRunSerializer

    def create(self, request, *args, **kwargs):
        ser = S.RunCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        try:
            if d.get("areas"):
                batch = create_batch(
                    d["notebook"],
                    d["parameters"],
                    d["areas"],
                    user=request.user,
                    trigger=TriggerKind.API,
                    notify_emails=d["notify_emails"],
                    stac_item=d.get("stac_item"),
                    filters=d["filters"],
                )
                return Response(S.RunBatchSerializer(batch).data, status=status.HTTP_202_ACCEPTED)
            run = create_run(
                d["notebook"],
                d["parameters"],
                area=d.get("area"),
                stac_item=d.get("stac_item"),
                filters=d["filters"],
                user=request.user,
                trigger=TriggerKind.API,
                notify_emails=d["notify_emails"],
            )
        except ParameterError as exc:
            return Response({"parameters": exc.errors}, status=400)
        except RunRequestError as exc:
            return Response({"detail": str(exc)}, status=400)
        return Response(
            S.NotebookRunSerializer(run, context={"request": request}).data, status=status.HTTP_202_ACCEPTED
        )

    @action(
        detail=False,
        methods=["post"],
        permission_classes=[IsAdminUser],
        parser_classes=[MultiPartParser, FormParser],
    )
    def upload(self, request):
        """Publish an already executed notebook (staff only); it is rendered, not executed."""
        ser = S.UploadCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        try:
            run = create_upload(
                d["file"],
                filename=d["file"].name,
                notebook_path=d.get("notebook_path", ""),
                title=d.get("title", ""),
                summary=d.get("summary", ""),
                description=d.get("description", ""),
                area=d.get("area"),
                stac_item=d.get("stac_item"),
                filters=d["filters"],
                user=request.user,
            )
        except UploadError as exc:
            return Response({"file": [str(exc)]}, status=400)
        return Response(
            S.NotebookRunSerializer(run, context={"request": request}).data, status=status.HTTP_202_ACCEPTED
        )

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated])
    def cancel(self, request, pk=None):
        run = self.get_object()
        cancel_run(run)
        return Response(S.NotebookRunSerializer(run, context={"request": request}).data)

    @action(detail=True, methods=["post"], permission_classes=[IsAuthenticated])
    def retry(self, request, pk=None):
        run = retry_run(self.get_object(), user=request.user)
        return Response(
            S.NotebookRunSerializer(run, context={"request": request}).data, status=status.HTTP_202_ACCEPTED
        )


class RunBatchViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = RunBatch.objects.select_related("notebook")
    serializer_class = S.RunBatchSerializer


class ReadAuthenticatedWriteStaff(BasePermission):
    def has_permission(self, request, view):
        user = request.user
        if request.method in SAFE_METHODS:
            return bool(user and user.is_authenticated)
        return bool(user and user.is_staff)


class StacCatalogViewSet(viewsets.ModelViewSet):
    queryset = StacCatalog.objects.all()
    serializer_class = S.StacCatalogSerializer
    permission_classes = [ReadAuthenticatedWriteStaff]
    lookup_field = "slug"
    pagination_class = None
    filterset_fields = ["profile", "is_active"]

    @action(detail=True, methods=["get"])
    def collections(self, request, slug=None):
        """Collections offered by the catalog (fetched live)."""
        catalog = self.get_object()
        try:
            with catalog.client() as client:
                collections = client.collections()
        except StacClientError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        return Response([{"id": c.get("id"), "title": c.get("title")} for c in collections])


class StacItemViewSet(FacetFilterMixin, viewsets.ReadOnlyModelViewSet):
    queryset = StacItem.objects.select_related("catalog").prefetch_related("facet_options__facet")
    serializer_class = S.StacItemSerializer
    filterset_fields = ["catalog__slug", "collection", "correlation_id"]
    search_fields = ["title", "correlation_id", "item_id"]
    ordering_fields = ["occurred_at", "first_seen_at"]


class TriggerRuleViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = TriggerRule.objects.select_related("notebook", "catalog").prefetch_related(
        "facet_options__facet"
    )
    serializer_class = S.TriggerRuleSerializer
    pagination_class = None


geojson_cached = cache_page(60 * 10)
