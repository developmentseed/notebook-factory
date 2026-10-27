from rest_framework import serializers

from factory.catalog.models import AdminArea, Country, Hazard
from factory.events.matching import fetch_item
from factory.events.models import StacCatalog, StacItem, TriggerRule
from factory.events.stac_client import StacClientError
from factory.notebooks import parameters as P
from factory.notebooks.models import AnalysisNotebook, NotebookRun, RunBatch, RunLogEntry


class HazardSerializer(serializers.ModelSerializer):
    class Meta:
        model = Hazard
        fields = ["key", "label", "icon", "color", "monty_codes"]


class CountrySerializer(serializers.ModelSerializer):
    class Meta:
        model = Country
        fields = ["iso3", "iso2", "name"]


class AdminAreaSerializer(serializers.ModelSerializer):
    country_name = serializers.CharField(source="country.name", read_only=True)

    class Meta:
        model = AdminArea
        fields = [
            "id",
            "code",
            "name",
            "level",
            "country",
            "country_name",
            "parent",
            "source",
            "bbox",
            "area_km2",
        ]


class AnalysisNotebookSerializer(serializers.ModelSerializer):
    hazards = HazardSerializer(many=True, read_only=True)
    use_case_display = serializers.CharField(source="get_use_case_display", read_only=True)
    parameter_defaults = serializers.SerializerMethodField()
    run_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = AnalysisNotebook
        fields = [
            "slug",
            "title",
            "summary",
            "description",
            "use_case",
            "use_case_display",
            "hazards",
            "source_kind",
            "source_repo",
            "source_ref",
            "notebook_path",
            "version",
            "parameter_schema",
            "parameter_defaults",
            "requires_area",
            "area_levels",
            "requires_stac_item",
            "stac_hints",
            "supports_writeback",
            "estimated_runtime_minutes",
            "is_active",
            "run_count",
            "created_at",
            "updated_at",
        ]

    def get_parameter_defaults(self, obj):
        return P.defaults(obj.parameter_schema)


class StacCatalogSerializer(serializers.ModelSerializer):
    """The API key is write-only: it is never returned, only whether one is set."""

    api_key = serializers.CharField(
        write_only=True, required=False, allow_blank=True, help_text="Sent as 'Authorization: Bearer <key>'"
    )
    has_api_key = serializers.SerializerMethodField()

    class Meta:
        model = StacCatalog
        fields = [
            "id",
            "name",
            "slug",
            "url",
            "api_key",
            "has_api_key",
            "profile",
            "writeback_enabled",
            "is_active",
            "last_checked_at",
            "last_error",
        ]
        read_only_fields = ["last_checked_at", "last_error"]

    def get_has_api_key(self, obj) -> bool:
        return bool(obj.api_key)


class StacItemSerializer(serializers.ModelSerializer):
    hazard = HazardSerializer(read_only=True)
    catalog = serializers.SlugRelatedField(slug_field="slug", read_only=True)

    class Meta:
        model = StacItem
        fields = [
            "id",
            "catalog",
            "item_id",
            "collection",
            "correlation_id",
            "title",
            "description",
            "roles",
            "hazard",
            "hazard_codes",
            "country_codes",
            "source",
            "occurred_at",
            "ended_at",
            "bbox",
            "stac_url",
            "first_seen_at",
            "updated_at",
        ]


class RunLogEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = RunLogEntry
        fields = ["created_at", "level", "message"]


class NotebookRunSerializer(serializers.ModelSerializer):
    notebook = serializers.SlugRelatedField(slug_field="slug", read_only=True)
    notebook_title = serializers.CharField(source="notebook.title", read_only=True)
    area = AdminAreaSerializer(read_only=True)
    hazard = HazardSerializer(read_only=True)
    stac_item = StacItemSerializer(read_only=True)
    triggered_by = serializers.CharField(source="triggered_by.username", read_only=True, default=None)
    title = serializers.CharField(read_only=True)
    subtitle = serializers.CharField(read_only=True)
    detail_url = serializers.SerializerMethodField()
    output_url = serializers.SerializerMethodField()
    notebook_url = serializers.SerializerMethodField()

    class Meta:
        model = NotebookRun
        fields = [
            "id",
            "notebook",
            "notebook_title",
            "title",
            "subtitle",
            "status",
            "parameters",
            "area",
            "hazard",
            "stac_item",
            "trigger",
            "triggered_by",
            "batch",
            "progress",
            "error",
            "output_url",
            "notebook_url",
            "artifacts",
            "writeback_status",
            "writeback_ref",
            "created_at",
            "started_at",
            "finished_at",
            "detail_url",
        ]

    def _abs(self, url):
        request = self.context.get("request")
        if url and url.startswith("/") and request:
            return request.build_absolute_uri(url)
        return url

    def get_detail_url(self, obj):
        return self._abs(obj.get_absolute_url())

    def get_output_url(self, obj):
        return self._abs(obj.output_url)

    def get_notebook_url(self, obj):
        return self._abs(obj.notebook_url)


class NotebookRunDetailSerializer(NotebookRunSerializer):
    log = RunLogEntrySerializer(source="log_entries", many=True, read_only=True)

    class Meta(NotebookRunSerializer.Meta):
        fields = NotebookRunSerializer.Meta.fields + ["log", "notify_emails"]


class RunCreateSerializer(serializers.Serializer):
    """POST /api/runs/ — one area, several areas, or a whole admin level of a country.

    The STAC item is either a stored one (`stac_item`, pk) or fetched live from
    `stac_catalog` (slug) + `stac_collection` + `stac_item_id`.
    """

    notebook = serializers.SlugRelatedField(
        slug_field="slug", queryset=AnalysisNotebook.objects.filter(is_active=True)
    )
    parameters = serializers.DictField(required=False, default=dict)
    area = serializers.PrimaryKeyRelatedField(
        queryset=AdminArea.objects.all(), required=False, allow_null=True
    )
    areas = serializers.PrimaryKeyRelatedField(queryset=AdminArea.objects.all(), many=True, required=False)
    country = serializers.CharField(
        required=False, help_text="ISO3, with area_level: fan out over that level"
    )
    area_level = serializers.IntegerField(required=False, min_value=0, max_value=5)
    stac_item = serializers.PrimaryKeyRelatedField(
        queryset=StacItem.objects.all(), required=False, allow_null=True
    )
    stac_catalog = serializers.SlugRelatedField(
        slug_field="slug", queryset=StacCatalog.objects.filter(is_active=True), required=False
    )
    stac_collection = serializers.CharField(required=False)
    stac_item_id = serializers.CharField(required=False)
    notify_emails = serializers.ListField(child=serializers.EmailField(), required=False, default=list)

    def validate(self, attrs):
        if attrs.get("country") and attrs.get("area_level") is not None:
            attrs["areas"] = list(
                AdminArea.objects.filter(
                    country_id=attrs["country"].upper(), level=attrs["area_level"]
                ).light()
            )
            if not attrs["areas"]:
                raise serializers.ValidationError({"country": "No areas loaded for that country/level."})
        live = [attrs.get("stac_catalog"), attrs.get("stac_collection"), attrs.get("stac_item_id")]
        if any(live):
            if not all(live):
                raise serializers.ValidationError(
                    {"stac_item_id": "stac_catalog, stac_collection and stac_item_id go together."}
                )
            try:
                attrs["stac_item"] = fetch_item(*live)
            except StacClientError as exc:
                raise serializers.ValidationError({"stac_item_id": str(exc)}) from exc
        return attrs


class RunBatchSerializer(serializers.ModelSerializer):
    notebook = serializers.SlugRelatedField(slug_field="slug", read_only=True)
    status_counts = serializers.SerializerMethodField()

    class Meta:
        model = RunBatch
        fields = ["id", "notebook", "description", "parameters", "trigger", "created_at", "status_counts"]

    def get_status_counts(self, obj):
        return obj.status_counts()


class TriggerRuleSerializer(serializers.ModelSerializer):
    notebook = serializers.SlugRelatedField(slug_field="slug", read_only=True)
    catalog = serializers.SlugRelatedField(slug_field="slug", read_only=True)
    hazards = HazardSerializer(many=True, read_only=True)

    class Meta:
        model = TriggerRule
        fields = [
            "id",
            "name",
            "enabled",
            "catalog",
            "notebook",
            "collections",
            "search_filter",
            "roles",
            "hazards",
            "country_codes",
            "parameter_template",
            "area_level",
            "rerun_on_update",
            "last_triggered_at",
        ]
