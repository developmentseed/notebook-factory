from rest_framework import serializers

from factory.catalog.facets import group_options
from factory.catalog.models import AdminArea, Country, Facet, FacetOption
from factory.events import refs as event_refs
from factory.events.matching import fetch_item
from factory.events.models import StacCatalog, StacItem, TriggerRule
from factory.events.stac_client import StacClientError
from factory.notebooks import parameters as P
from factory.notebooks.models import AnalysisNotebook, NotebookRun, RunBatch, RunLogEntry, TemplateVersion


class FacetOptionSerializer(serializers.ModelSerializer):
    facet = serializers.SlugRelatedField(slug_field="key", queryset=Facet.objects.all())

    class Meta:
        model = FacetOption
        fields = ["id", "facet", "key", "label", "icon", "color", "match_codes", "order"]


class FacetSerializer(serializers.ModelSerializer):
    options = serializers.SerializerMethodField()

    class Meta:
        model = Facet
        fields = [
            "id",
            "key",
            "label",
            "description",
            "multiple",
            "stac_property",
            "show_in_browse",
            "order",
            "options",
        ]

    def get_options(self, obj) -> list[dict]:
        return [
            {"key": o.key, "label": o.label, "icon": o.icon, "color": o.color, "match_codes": o.match_codes}
            for o in obj.options.all()
        ]


class FiltersField(serializers.ReadOnlyField):
    """{facet_key: [{key, label, icon, color}]} from `facet_groups` (or a list of options)."""

    def __init__(self, **kwargs):
        kwargs.setdefault("source", "facet_groups")
        super().__init__(**kwargs)

    def to_representation(self, groups):
        groups = list(groups)
        if groups and isinstance(groups[0], FacetOption):
            groups = group_options(groups)
        return {
            facet.key: [{"key": o.key, "label": o.label, "icon": o.icon, "color": o.color} for o in options]
            for facet, options in groups
        }


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


class TemplateVersionSerializer(serializers.ModelSerializer):
    created_by = serializers.StringRelatedField()
    is_current = serializers.BooleanField(read_only=True)

    class Meta:
        model = TemplateVersion
        fields = [
            "number",
            "label",
            "note",
            "is_current",
            "notebook_path",
            "sha256",
            "files",
            "source_repo",
            "source_ref",
            "parameter_schema",
            "requires_area",
            "area_levels",
            "requires_stac_item",
            "stac_hints",
            "supports_writeback",
            "estimated_runtime_minutes",
            "secret_names",
            "created_by",
            "created_at",
        ]


class CurrentVersionField(serializers.SerializerMethodField):
    def to_representation(self, obj):
        v = obj.current_version
        if v is None:
            return None
        return {
            "number": v.number,
            "label": v.label,
            "sha256": v.sha256,
            "notebook_path": v.notebook_path,
            "created_at": v.created_at,
        }


class AnalysisNotebookSerializer(serializers.ModelSerializer):
    filters = FiltersField()
    parameter_defaults = serializers.SerializerMethodField()
    run_count = serializers.IntegerField(read_only=True)
    current_version = CurrentVersionField()

    class Meta:
        model = AnalysisNotebook
        fields = [
            "slug",
            "title",
            "summary",
            "description",
            "filters",
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
            "secret_names",
            "is_active",
            "current_version",
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
    filters = FiltersField()
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
            "filters",
            "country_codes",
            "source",
            "occurred_at",
            "ended_at",
            "bbox",
            "event_refs",
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
    notebook_title = serializers.CharField(source="notebook.title", read_only=True, default=None)
    template_version = serializers.SlugRelatedField(
        slug_field="number", read_only=True, help_text="Template version number the run executes"
    )
    is_upload = serializers.BooleanField(read_only=True)
    area = AdminAreaSerializer(read_only=True)
    filters = FiltersField()
    stac_item = StacItemSerializer(read_only=True)
    disaster_refs = serializers.ListField(
        source="all_event_refs",
        child=serializers.CharField(),
        read_only=True,
        help_text="The run's disaster refs plus its STAC item's",
    )
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
            "template_version",
            "is_upload",
            "title",
            "subtitle",
            "summary",
            "description",
            "status",
            "parameters",
            "area",
            "filters",
            "stac_item",
            "event_refs",
            "disaster_refs",
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


class EventRefsField(serializers.ListField):
    """Disaster IDs in any form `factory.events.refs.parse` accepts, stored as refs."""

    child = serializers.CharField()

    def __init__(self, **kwargs):
        kwargs.setdefault("required", False)
        kwargs.setdefault("default", list)
        kwargs.setdefault(
            "help_text",
            'Disasters this run is about, e.g. ["EQ-2025-000043-MMR", "go:7464", "EMSR798"]',
        )
        super().__init__(**kwargs)

    def to_internal_value(self, data):
        if isinstance(data, str):  # multipart forms send one string
            data = [t for t in data.replace("\n", ",").split(",") if t.strip()]
        out, unknown = [], []
        for token in super().to_internal_value(data):
            parsed = event_refs.parse(token)
            (out.extend(parsed) if parsed else unknown.append(token))
        if unknown:
            raise serializers.ValidationError(f"Not a disaster ID we recognise: {', '.join(unknown)}")
        return list(dict.fromkeys(out))


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
    event_refs = EventRefsField()
    filters = serializers.DictField(
        child=serializers.ListField(child=serializers.CharField()),
        required=False,
        default=dict,
        help_text='Manual filter tags, e.g. {"hazard": ["flood"]}; they win over derived ones',
    )

    def validate_filters(self, value):
        return _validate_filter_keys(value)

    def validate(self, attrs):
        if attrs.get("country") and attrs.get("area_level") is not None:
            attrs["areas"] = list(
                AdminArea.objects.filter(
                    country_id=attrs["country"].upper(), level=attrs["area_level"]
                ).light()
            )
            if not attrs["areas"]:
                raise serializers.ValidationError({"country": "No areas loaded for that country/level."})
        return _resolve_live_stac_item(attrs)


def _resolve_live_stac_item(attrs):
    """Fetch and store the item named by stac_catalog + stac_collection + stac_item_id, if given."""
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


def _validate_filter_keys(value):
    unknown = set(value) - set(Facet.objects.values_list("key", flat=True))
    if unknown:
        raise serializers.ValidationError(f"Unknown filter(s): {', '.join(sorted(unknown))}")
    return value


class UploadCreateSerializer(serializers.Serializer):
    """POST /api/runs/upload/ (multipart) — publish an already executed notebook (.zip or .ipynb)."""

    file = serializers.FileField()
    notebook_path = serializers.CharField(
        required=False, allow_blank=True, help_text="Notebook inside the zip; needed when it holds several"
    )
    title = serializers.CharField(required=False, allow_blank=True, max_length=200)
    summary = serializers.CharField(required=False, allow_blank=True, max_length=300)
    description = serializers.CharField(required=False, allow_blank=True)
    area = serializers.PrimaryKeyRelatedField(
        queryset=AdminArea.objects.all(), required=False, allow_null=True
    )
    stac_item = serializers.PrimaryKeyRelatedField(
        queryset=StacItem.objects.all(), required=False, allow_null=True
    )
    stac_catalog = serializers.SlugRelatedField(
        slug_field="slug", queryset=StacCatalog.objects.filter(is_active=True), required=False
    )
    stac_collection = serializers.CharField(required=False)
    stac_item_id = serializers.CharField(required=False)
    event_refs = EventRefsField()
    filters = serializers.JSONField(
        binary=True,
        required=False,
        default=dict,
        help_text='Filter tags as a JSON object, e.g. {"hazard": ["flood"]}',
    )

    def validate_filters(self, value):
        if not isinstance(value, dict) or not all(isinstance(v, list) for v in value.values()):
            raise serializers.ValidationError('Expected an object of lists, e.g. {"hazard": ["flood"]}.')
        return _validate_filter_keys(value)

    def validate(self, attrs):
        return _resolve_live_stac_item(attrs)


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
    filters = FiltersField(source="facet_options.all")

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
            "filters",
            "country_codes",
            "parameter_template",
            "area_level",
            "rerun_on_update",
            "last_triggered_at",
        ]


def _metadata(value):
    """template.yml overrides: a JSON object, or YAML / JSON text (multipart forms send text)."""
    import yaml

    if isinstance(value, str):
        try:
            value = yaml.safe_load(value) if value.strip() else {}
        except yaml.YAMLError as exc:
            raise serializers.ValidationError(f"Not valid YAML / JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise serializers.ValidationError("Expected an object with template.yml keys.")
    return value


class TemplateCreateSerializer(serializers.Serializer):
    """POST /api/templates/ (multipart): a new template from a .zip or .ipynb. It starts inactive."""

    file = serializers.FileField()
    notebook_path = serializers.CharField(
        required=False, allow_blank=True, help_text="Notebook inside the zip; needed when it holds several"
    )
    slug = serializers.SlugField(required=False, allow_blank=True, max_length=80)
    title = serializers.CharField(required=False, allow_blank=True, max_length=200)
    note = serializers.CharField(required=False, allow_blank=True, max_length=300)
    metadata = serializers.JSONField(
        binary=True,
        required=False,
        default=dict,
        help_text="template.yml keys; override the zip's template.yml",
    )

    def validate_metadata(self, value):
        return _metadata(value)


class TemplateVersionCreateSerializer(TemplateCreateSerializer):
    """POST /api/templates/<slug>/versions/ (multipart): a new version of the bundle."""

    slug = None
    title = None
    make_current = serializers.BooleanField(required=False, default=True)


class TemplateUpdateSerializer(serializers.Serializer):
    """PATCH /api/templates/<slug>/: fields that are not versioned."""

    title = serializers.CharField(required=False, max_length=200)
    summary = serializers.CharField(required=False, allow_blank=True, max_length=300)
    description = serializers.CharField(required=False, allow_blank=True)
    is_active = serializers.BooleanField(required=False)
    filters = serializers.JSONField(
        required=False, help_text='e.g. {"hazard": ["flood"], "use_case": ["uc1"]}'
    )

    def validate_filters(self, value):
        if not isinstance(value, dict) or not all(isinstance(v, list) for v in value.values()):
            raise serializers.ValidationError('Expected an object of lists, e.g. {"hazard": ["flood"]}.')
        return _validate_filter_keys(value)


class DisasterMemberSerializer(serializers.Serializer):
    ref = serializers.CharField()
    source = serializers.CharField()
    collection = serializers.CharField()
    item_id = serializers.CharField()
    title = serializers.CharField()
    start = serializers.DateTimeField(allow_null=True)
    countries = serializers.ListField(child=serializers.CharField())
    magnitude = serializers.FloatField(allow_null=True)
    score = serializers.FloatField()
    reason = serializers.CharField()
    stac_url = serializers.CharField()


class DisasterSerializer(serializers.Serializer):
    key = serializers.CharField(help_text="Canonical ref; /disasters/<key>/ in the web UI")
    title = serializers.CharField()
    countries = serializers.ListField(child=serializers.CharField())
    hazard_codes = serializers.ListField(child=serializers.CharField())
    start = serializers.DateTimeField(allow_null=True)
    end = serializers.DateTimeField(allow_null=True)
    id_refs = serializers.ListField(child=serializers.CharField(), help_text="One ref per source id")
    refs = serializers.ListField(child=serializers.CharField(), help_text="Every ref; runs match on these")
    anchor = DisasterMemberSerializer()
    members = DisasterMemberSerializer(many=True)
    possible = DisasterMemberSerializer(many=True)
    error = serializers.CharField()
    runs = serializers.SerializerMethodField()

    def get_runs(self, obj) -> list[str]:
        return [str(pk) for pk in self.context.get("run_ids", [])]
