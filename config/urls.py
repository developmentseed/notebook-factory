from django.conf import settings
from django.contrib import admin
from django.urls import include, path

from factory.web.views import published_file

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("factory.api.urls")),
    path("", include("factory.web.urls")),
]

if settings.SERVE_PUBLISHED_LOCALLY:
    # Dev-only: serve published notebooks straight from the local "object storage" directory.
    urlpatterns.append(
        path(settings.PUBLISHED_URL.strip("/") + "/<path:path>", published_file, name="published-local")
    )

if settings.DEBUG and "debug_toolbar" in settings.INSTALLED_APPS:
    urlpatterns.append(path("__debug__/", include("debug_toolbar.urls")))
