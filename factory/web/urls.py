from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

app_name = "web"
urlpatterns = [
    path("", views.browse, name="browse"),
    path("templates/", views.template_list, name="template-list"),
    path("templates/<slug:slug>/", views.template_detail, name="template-detail"),
    path("templates/<slug:slug>/run/", views.run_new, name="run-new"),
    path("runs/<uuid:pk>/", views.run_detail, name="run-detail"),
    path("runs/<uuid:pk>/status.json", views.run_status_json, name="run-status"),
    path("runs/<uuid:pk>/view/", views.run_view, name="run-view"),
    path("runs/<uuid:pk>/cancel/", views.run_cancel, name="run-cancel"),
    path("runs/<uuid:pk>/retry/", views.run_retry, name="run-retry"),
    path("batches/<uuid:pk>/", views.batch_detail, name="batch-detail"),
    path("templates/<slug:slug>/run/stac-picker/", views.stac_picker_partial, name="stac-picker"),
    path("items/", views.stac_item_list, name="stac-item-list"),
    path("items/<int:pk>/", views.stac_item_detail, name="stac-item-detail"),
    path("runs/<uuid:pk>/tags/", views.run_tags, name="run-tags"),
    path("runs/<uuid:pk>/details/", views.run_details, name="run-details"),
    path("uploads/new/", views.upload_new, name="upload-new"),
    path("uploads/inspect/", views.upload_inspect, name="upload-inspect"),
    path("uploads/stac-picker/", views.upload_stac_picker, name="upload-stac-picker"),
    path("filters/", views.facet_list, name="facet-list"),
    path("filters/-/new/", views.facet_edit, name="facet-new"),
    path("filters/<slug:key>/", views.facet_edit, name="facet-edit"),
    path("filters/<slug:key>/delete/", views.facet_delete, name="facet-delete"),
    path("catalogs/", views.catalog_list, name="catalog-list"),
    path("catalogs/new/", views.catalog_edit, name="catalog-new"),
    path("catalogs/-/test/", views.catalog_test, name="catalog-test"),
    path("catalogs/<slug:slug>/", views.catalog_edit, name="catalog-edit"),
    path("catalogs/<slug:slug>/delete/", views.catalog_delete, name="catalog-delete"),
    path("notifications/", views.notification_list, name="notifications"),
    path("notifications/<int:pk>/", views.notification_open, name="notification-open"),
    path("accounts/login/", auth_views.LoginView.as_view(), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("help/", views.help_page, name="help"),
    path("healthz", views.healthz, name="healthz"),
]
