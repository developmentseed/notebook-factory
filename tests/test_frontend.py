import json

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.urls import resolve

from factory.web.frontend import frontend


@pytest.fixture
def frontend_assets(settings, tmp_path):
    settings.FRONTEND_DEV_SERVER_URL = ""
    settings.FRONTEND_DIST_DIR = tmp_path
    (tmp_path / ".vite").mkdir()
    (tmp_path / ".vite" / "manifest.json").write_text(
        json.dumps(
            {
                "src/main.tsx": {
                    "file": "assets/main-test.js",
                    "css": ["assets/main-test.css"],
                    "imports": ["_shared.js"],
                },
                "_shared.js": {"file": "assets/shared-test.js", "css": ["assets/shared-test.css"]},
            }
        )
    )


@pytest.mark.parametrize("path", ["/explore", "/explore/", "/build", "/build/", "/build/templates/example"])
def test_frontend_shell_and_history_fallback(client, frontend_assets, path):
    response = client.get(path)
    assert response.status_code == 200
    assert b'id="react-root"' in response.content
    assert b"/static/frontend/assets/main-test.js" in response.content
    assert b"/static/frontend/assets/shared-test.css" in response.content
    assert b'rel="modulepreload"' in response.content
    assert b"htmx.org" not in response.content
    assert response.context["frontend_config"]["user"] is None
    assert "no-store" in response["Cache-Control"]


def test_frontend_keeps_django_sessions_and_csrf(client, admin_user, frontend_assets):
    client.force_login(admin_user)
    response = client.get("/build")
    config = response.context["frontend_config"]
    assert config["user"] == {"username": admin_user.username, "isStaff": True}
    assert config["adminUrl"] == "/admin/"
    assert config["logoutUrl"] == "/accounts/logout/"
    assert config["csrfToken"]
    assert "csrftoken" in response.cookies


@pytest.mark.parametrize("path", ["/admin/", "/api/templates/", "/templates/", "/accounts/login/", "/"])
def test_frontend_does_not_capture_existing_routes(path):
    assert resolve(path).func is not frontend


def test_vite_development_assets(client, settings):
    settings.DEBUG = True
    settings.FRONTEND_DEV_SERVER_URL = "http://127.0.0.1:5173"
    response = client.get("/explore")
    assert b"http://127.0.0.1:5173/static/frontend/@vite/client" in response.content
    assert b"http://127.0.0.1:5173/static/frontend/src/main.tsx" in response.content
    assert b"__vite_plugin_react_preamble_installed__" in response.content


def test_production_ignores_vite_server(client, settings, frontend_assets):
    settings.DEBUG = False
    settings.FRONTEND_DEV_SERVER_URL = "http://127.0.0.1:5173"
    response = client.get("/explore")
    assert b"5173" not in response.content
    assert b"main-test.js" in response.content


def test_missing_frontend_build_has_actionable_error(client, settings, tmp_path):
    settings.FRONTEND_DEV_SERVER_URL = ""
    settings.FRONTEND_DIST_DIR = tmp_path
    with pytest.raises(ImproperlyConfigured, match="npm ci --prefix frontend"):
        client.get("/explore")
