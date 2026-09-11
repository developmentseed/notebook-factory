from django.conf import settings
from django.db.models import Count
from django.http import JsonResponse
from django.views.decorators.cache import cache_page
from django_filters import rest_framework as filters
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated, IsAuthenticatedOrReadOnly
from rest_framework.response import Response

from factory.catalog.models import AdminArea, Country, Hazard
from factory.events.models import Event, EventTriggerRule
from factory.notebooks.models import AnalysisNotebook, NotebookRun, RunBatch, TriggerKind
from factory.notebooks.parameters import ParameterError
from factory.notebooks.services import RunRequestError, cancel_run, create_batch, create_run, retry_run

from . import serializers as S


class HazardViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Hazard.objects.all()
    serializer_class = S.HazardSerializer
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


class AnalysisNotebookViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = (
        AnalysisNotebook.objects.filter(is_active=True)
        .prefetch_related("hazards")
        .annotate(run_count=Count("runs"))
    )
    serializer_class = S.AnalysisNotebookSerializer
    lookup_field = "slug"
    filterset_fields = ["use_case", "hazards__key", "requires_event", "requires_area"]
    search_fields = ["title", "summary", "description"]


class RunFilter(filters.FilterSet):
    notebook = filters.CharFilter(field_name="notebook__slug")
    use_case = filters.CharFilter(field_name="notebook__use_case")
    country = filters.CharFilter(method="filter_country")
    area = filters.NumberFilter(field_name="area_id")
    hazard = filters.CharFilter(field_name="hazard__key")
    event = filters.NumberFilter(field_name="event_id")
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

        return qs.filter(Q(area__country_id=value) | Q(event__country_codes__contains=[value]))


class NotebookRunViewSet(
    mixins.CreateModelMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    queryset = NotebookRun.objects.with_related()
    filterset_class = RunFilter
    search_fields = ["notebook__title", "area__name", "event__title", "event__monty_corr_id"]
    ordering_fields = ["created_at", "finished_at", "status"]
    permission_classes = [IsAuthenticatedOrReadOnly]

    def get_serializer_class(self):
        if self.action == "create":
            return S.RunCreateSerializer
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
                    event=d.get("event"),
                )
                return Response(S.RunBatchSerializer(batch).data, status=status.HTTP_202_ACCEPTED)
            run = create_run(
                d["notebook"],
                d["parameters"],
                area=d.get("area"),
                event=d.get("event"),
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


class EventViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Event.objects.select_related("hazard")
    serializer_class = S.EventSerializer
    filterset_fields = ["collection", "hazard__key", "monty_corr_id"]
    search_fields = ["title", "monty_corr_id", "stac_id"]
    ordering_fields = ["occurred_at", "first_seen_at"]


class EventTriggerRuleViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = EventTriggerRule.objects.select_related("notebook").prefetch_related("hazards")
    serializer_class = S.EventTriggerRuleSerializer
    pagination_class = None


geojson_cached = cache_page(60 * 10)
