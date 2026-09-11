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
    path("events/", views.event_list, name="event-list"),
    path("events/<int:pk>/", views.event_detail, name="event-detail"),
    path("notifications/", views.notification_list, name="notifications"),
    path("notifications/<int:pk>/", views.notification_open, name="notification-open"),
    path("accounts/login/", auth_views.LoginView.as_view(), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("healthz", views.healthz, name="healthz"),
]
