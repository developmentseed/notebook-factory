"""
Django settings for the Montandon Notebook Factory.

Everything that differs between environments is read from environment variables
(or a `.env` file in the project root). See `.env.example` for the full list.
"""

import platform
import shutil
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env()
if (BASE_DIR / ".env").exists():
    environ.Env.read_env(BASE_DIR / ".env")

# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------
SECRET_KEY = env("SECRET_KEY", default="dev-only-insecure-secret-key-change-me")
DEBUG = env.bool("DEBUG", default=True)
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1", "0.0.0.0"])
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])
SITE_NAME = env("SITE_NAME", default="Montandon Notebooks")
# Public base URL of this app (used in notification links).
PUBLIC_BASE_URL = env("PUBLIC_BASE_URL", default="http://localhost:8000")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "django.contrib.gis",
    "rest_framework",
    "rest_framework.authtoken",
    "django_filters",
    "drf_spectacular",
    "django_htmx",
    "corsheaders",
    "storages",
    "factory.catalog",
    "factory.notebooks",
    "factory.events",
    "factory.notifications",
    "factory.api",
    "factory.web",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "factory" / "web" / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "factory.web.context_processors.site",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# ---------------------------------------------------------------------------
# Database (PostGIS)
# ---------------------------------------------------------------------------
DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default="postgis://postgres:postgres@localhost:5432/notebook_factory",
    )
}
DATABASES["default"]["ENGINE"] = "django.contrib.gis.db.backends.postgis"
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DB_CONN_MAX_AGE", default=60)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


def _autodetect_lib(env_name: str, brew_formula: str, lib_glob: str) -> str | None:
    """Find GDAL/GEOS on macOS Homebrew installs so the GIS backend works without docker."""
    explicit = env(env_name, default=None)
    if explicit:
        return explicit
    if platform.system() != "Darwin":
        return None
    for prefix in ("/opt/homebrew/opt", "/usr/local/opt"):
        candidates = sorted(Path(prefix, brew_formula, "lib").glob(lib_glob))
        if candidates:
            return str(candidates[0])
    return None


GDAL_LIBRARY_PATH = _autodetect_lib("GDAL_LIBRARY_PATH", "gdal", "libgdal.dylib")
GEOS_LIBRARY_PATH = _autodetect_lib("GEOS_LIBRARY_PATH", "geos", "libgeos_c.dylib")

# ---------------------------------------------------------------------------
# Auth / i18n
# ---------------------------------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LOGIN_URL = "web:login"
LOGIN_REDIRECT_URL = "web:browse"
LOGOUT_REDIRECT_URL = "web:browse"

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# Static & media
# ---------------------------------------------------------------------------
STATIC_URL = "/static/"
STATIC_ROOT = env("STATIC_ROOT", default=str(BASE_DIR / "staticfiles"))
MEDIA_URL = "/media/"
MEDIA_ROOT = env("MEDIA_ROOT", default=str(BASE_DIR / "data" / "media"))

# ---------------------------------------------------------------------------
# Published-output storage ("object storage" for rendered notebooks)
#
# PUBLISHED_STORAGE_BACKEND = local | s3 | azure
#   local  -> files under PUBLISHED_ROOT, served by Django at PUBLISHED_URL (dev only)
#   s3     -> any S3-compatible bucket (AWS, MinIO, ...) via django-storages
#   azure  -> Azure Blob Storage via django-storages
# ---------------------------------------------------------------------------
PUBLISHED_STORAGE_BACKEND = env("PUBLISHED_STORAGE_BACKEND", default="local")
PUBLISHED_ROOT = env("PUBLISHED_ROOT", default=str(BASE_DIR / "data" / "published"))
PUBLISHED_URL = env("PUBLISHED_URL", default="/published/")
SERVE_PUBLISHED_LOCALLY = env.bool("SERVE_PUBLISHED_LOCALLY", default=PUBLISHED_STORAGE_BACKEND == "local")

_published_storage: dict
if PUBLISHED_STORAGE_BACKEND == "s3":
    _published_storage = {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": env("S3_BUCKET", default="notebooks"),
            "access_key": env("S3_ACCESS_KEY", default=None),
            "secret_key": env("S3_SECRET_KEY", default=None),
            "region_name": env("S3_REGION", default=None),
            "endpoint_url": env("S3_ENDPOINT_URL", default=None),
            "custom_domain": env("S3_CUSTOM_DOMAIN", default=None),
            "url_protocol": env("S3_URL_PROTOCOL", default="https:"),
            "querystring_auth": env.bool("S3_QUERYSTRING_AUTH", default=False),
            "default_acl": env("S3_DEFAULT_ACL", default=None),
            "file_overwrite": True,
            "addressing_style": env("S3_ADDRESSING_STYLE", default=None),
        },
    }
elif PUBLISHED_STORAGE_BACKEND == "azure":
    _azure_key = env("AZURE_ACCOUNT_KEY", default=None)
    _azure_connection_string = env("AZURE_CONNECTION_STRING", default=None)
    _azure_token_credential = None
    if not (_azure_key or _azure_connection_string):
        # No key: sign in as the pod's workload identity (or `az login` locally).
        from azure.identity import DefaultAzureCredential

        _azure_token_credential = DefaultAzureCredential()
    _published_storage = {
        "BACKEND": "storages.backends.azure_storage.AzureStorage",
        "OPTIONS": {
            "account_name": env("AZURE_ACCOUNT_NAME", default=None),
            "account_key": _azure_key,
            "connection_string": _azure_connection_string,
            "token_credential": _azure_token_credential,
            "azure_container": env("AZURE_CONTAINER", default="notebooks"),
            "custom_domain": env("AZURE_CUSTOM_DOMAIN", default=None),
            "expiration_secs": None,
            "overwrite_files": True,
        },
    }
else:
    _published_storage = {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
        "OPTIONS": {"location": PUBLISHED_ROOT, "base_url": PUBLISHED_URL},
    }

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
        "OPTIONS": {"location": MEDIA_ROOT, "base_url": MEDIA_URL},
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedStaticFilesStorage",
    },
    "published": _published_storage,
}

# Optional explicit public base for published files (e.g. a CDN in front of the bucket).
# When empty, the storage backend's own URL is used.
PUBLISHED_PUBLIC_BASE_URL = env("PUBLISHED_PUBLIC_BASE_URL", default="")

# ---------------------------------------------------------------------------
# Celery
#
# If no broker is configured, tasks run inline in a background thread of the
# web process ("eager" mode). That is enough for a laptop; use Redis + a worker
# for anything shared.
# ---------------------------------------------------------------------------
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default=CELERY_BROKER_URL or None)
CELERY_TASK_ALWAYS_EAGER = env.bool("CELERY_TASK_ALWAYS_EAGER", default=not CELERY_BROKER_URL)
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_TIME_LIMIT = env.int("CELERY_TASK_TIME_LIMIT", default=60 * 60 * 3)
# Redis hands an unacknowledged task to another worker after the visibility timeout (1 h by
# default). With acks_late, it must outlast the longest task or long runs execute twice.
CELERY_BROKER_TRANSPORT_OPTIONS = {"visibility_timeout": CELERY_TASK_TIME_LIMIT + 60 * 60}
CELERY_TIMEZONE = TIME_ZONE
CELERY_BEAT_SCHEDULE = {
    "poll-montandon": {
        "task": "factory.events.tasks.poll_montandon",
        "schedule": env.int("MONTANDON_POLL_INTERVAL_SECONDS", default=600),
    },
    "cleanup-workdirs": {
        "task": "factory.notebooks.tasks.cleanup_workdirs",
        "schedule": 60 * 60,
    },
}

# ---------------------------------------------------------------------------
# Notebook factory
# ---------------------------------------------------------------------------
NOTEBOOK_FACTORY = {
    # Directory holding local notebook templates (each in its own folder with template.yml).
    "TEMPLATES_DIR": Path(env("NOTEBOOK_TEMPLATES_DIR", default=str(BASE_DIR / "notebook_templates"))),
    # Scratch space for runs (inputs, executed notebook, MyST build).
    "WORK_DIR": Path(env("NOTEBOOK_WORK_DIR", default=str(BASE_DIR / "data" / "work"))),
    # Cache for git checkouts of remote template repos.
    "GIT_CACHE_DIR": Path(env("NOTEBOOK_GIT_CACHE_DIR", default=str(BASE_DIR / "data" / "git-cache"))),
    # Keep a run's work directory after it finishes (useful when debugging).
    "KEEP_WORKDIRS": env.bool("NOTEBOOK_KEEP_WORKDIRS", default=DEBUG),
    "WORKDIR_MAX_AGE_HOURS": env.int("NOTEBOOK_WORKDIR_MAX_AGE_HOURS", default=24),
    # Runs with no progress for this long are marked failed by the cleanup task.
    "STALE_RUN_HOURS": env.float("NOTEBOOK_STALE_RUN_HOURS", default=6),
    # Concurrency when running without a broker (eager mode).
    "EAGER_WORKERS": env.int("NOTEBOOK_EAGER_WORKERS", default=2),
    # Parallel uploads when publishing a run's files to object storage.
    "UPLOAD_WORKERS": env.int("NOTEBOOK_UPLOAD_WORKERS", default=8),
    # papermill
    "KERNEL_NAME": env("NOTEBOOK_KERNEL_NAME", default="python3"),
    "EXECUTION_TIMEOUT": env.int("NOTEBOOK_EXECUTION_TIMEOUT", default=60 * 60),
    "CELL_TIMEOUT": env.int("NOTEBOOK_CELL_TIMEOUT", default=60 * 20),
    # MyST rendering
    "MYST_COMMAND": env("MYST_COMMAND", default=shutil.which("myst") or "myst"),
    "MYST_TEMPLATE": env("MYST_TEMPLATE", default="book-theme"),
    "MYST_TEMPLATE_CACHE_DIR": Path(
        env("MYST_TEMPLATE_CACHE_DIR", default=str(BASE_DIR / "data" / "myst-templates"))
    ),
    "MYST_BUILD_TIMEOUT": env.int("MYST_BUILD_TIMEOUT", default=60 * 10),
    # Collapse code cells in the published page (readers can expand them; the .ipynb is downloadable).
    "MYST_HIDE_CODE": env.bool("MYST_HIDE_CODE", default=True),
    # The MyST theme loads thebe-core to run its Jupyter widget manager; stripping it breaks widget outputs.
    "MYST_STRIP_THEBE": env.bool("MYST_STRIP_THEBE", default=False),
    # Where published runs live inside the storage (prefix).
    "PUBLISH_PREFIX": env("NOTEBOOK_PUBLISH_PREFIX", default="runs"),
    # Simplification tolerance (degrees) for GeoJSON served to the map picker.
    "AREA_SIMPLIFY_TOLERANCE": env.float("AREA_SIMPLIFY_TOLERANCE", default=0.005),
}

# Montandon (STAC API) integration
MONTANDON = {
    "STAC_URL": env("MONTANDON_STAC_URL", default="https://montandon-eoapi-stage.ifrc.org/stac"),
    "API_TOKEN": env("MONTANDON_API_TOKEN", default=""),
    "POLL_OVERLAP_MINUTES": env.int("MONTANDON_POLL_OVERLAP_MINUTES", default=30),
    "POLL_LOOKBACK_DAYS": env.int("MONTANDON_POLL_LOOKBACK_DAYS", default=7),
    "POLL_PAGE_SIZE": env.int("MONTANDON_POLL_PAGE_SIZE", default=100),
    "WRITEBACK_ENABLED": env.bool("MONTANDON_WRITEBACK_ENABLED", default=False),
    "TIMEOUT": env.int("MONTANDON_TIMEOUT", default=60),
}

# ---------------------------------------------------------------------------
# REST framework
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework.authentication.TokenAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticatedOrReadOnly"],
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}
SPECTACULAR_SETTINGS = {
    "TITLE": "Montandon Notebooks API",
    "DESCRIPTION": "Index of analysis notebook templates and their published runs; orchestration of new runs.",
    "VERSION": "0.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
}
CORS_ALLOW_ALL_ORIGINS = env.bool("CORS_ALLOW_ALL_ORIGINS", default=DEBUG)
CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])

# ---------------------------------------------------------------------------
# Email / notifications
# ---------------------------------------------------------------------------
EMAIL_BACKEND = env("EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = env("EMAIL_HOST", default="localhost")
EMAIL_PORT = env.int("EMAIL_PORT", default=25)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=False)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="notebooks@example.org")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"simple": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "simple"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", default="INFO")},
    "loggers": {
        "django": {"level": "INFO"},
        "factory": {"level": env("LOG_LEVEL", default="INFO")},
        "papermill": {"level": "INFO", "propagate": False},
    },
}

if DEBUG:
    try:
        import debug_toolbar  # noqa: F401

        INSTALLED_APPS.append("debug_toolbar")
        MIDDLEWARE.insert(1, "debug_toolbar.middleware.DebugToolbarMiddleware")
        INTERNAL_IPS = ["127.0.0.1"]
        DEBUG_TOOLBAR_CONFIG = {
            "SHOW_TOOLBAR_CALLBACK": lambda request: env.bool("DEBUG_TOOLBAR", default=False)
        }
    except ImportError:
        pass
