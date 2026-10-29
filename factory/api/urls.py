from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("filters", views.FacetViewSet, basename="filter")
router.register("filter-options", views.FacetOptionViewSet, basename="filter-option")
router.register("countries", views.CountryViewSet, basename="country")
router.register("areas", views.AdminAreaViewSet, basename="area")
router.register("templates", views.AnalysisNotebookViewSet, basename="template")
router.register("runs", views.NotebookRunViewSet, basename="run")
router.register("batches", views.RunBatchViewSet, basename="batch")
router.register("catalogs", views.StacCatalogViewSet, basename="catalog")
router.register("stac-items", views.StacItemViewSet, basename="stac-item")
router.register("trigger-rules", views.TriggerRuleViewSet, basename="trigger-rule")

app_name = "api"
urlpatterns = [
    path("schema/", SpectacularAPIView.as_view(), name="schema"),
    path("docs/", SpectacularSwaggerView.as_view(url_name="api:schema"), name="docs"),
    path("auth/", include("rest_framework.urls")),
    path("disasters/resolve/", views.disaster_resolve, name="disaster-resolve"),
    path("", include(router.urls)),
]
