"""Django owns sessions and serves the shell for the public React routes."""

from django.conf import settings
from django.middleware.csrf import get_token
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET


@require_GET
@never_cache
def frontend(request):
    """Render the React shell with the current Django identity and CSRF-protected session."""
    return render_frontend(request)


def render_frontend(request, *, run=None):
    """Share the session shell with the Django-validated React run form, including failed POSTs."""
    user = request.user
    config = {
        "siteName": settings.SITE_NAME,
        "user": {"username": user.get_username(), "isStaff": user.is_staff}
        if user.is_authenticated
        else None,
        "csrfToken": get_token(request),
        "loginUrl": reverse("web:login"),
        "logoutUrl": reverse("web:logout"),
        "adminUrl": reverse("admin:index"),
        "run": run,
    }
    title = run["title"] if run else "Explore" if request.path.rstrip("/") == "/explore" else "Build"
    return render(request, "web/frontend.html", {"frontend_config": config, "frontend_title": title})
