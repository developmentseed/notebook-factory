"""
What a template's kernel sees in its environment.

Kernels do not inherit the worker's environment (which holds DATABASE_URL,
SECRET_KEY, storage keys…). They get:

* the allowlisted variables below (paths, locale, GDAL/PROJ, proxies, CA bundles),
  plus any names / `PREFIX_*` patterns in NOTEBOOK_KERNEL_ENV_PASSTHROUGH;
* per-run values: NOTEBOOK_RUN_ID, and STAC_API_URL / STAC_API_KEY for the run's item;
* the secrets the template declares (`secret_names`) *and* that are granted to it.

NOTEBOOK_KERNEL_ENV_INHERIT=true restores full inheritance. This keeps app
credentials out of a template's reach through the environment; it is not a
sandbox (the kernel runs as the same OS user as the worker).
"""

from __future__ import annotations

import os
from fnmatch import fnmatchcase

from django.conf import settings

KERNEL_ENV_ALLOW = (
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "SHELL",
    "LANG",
    "LANGUAGE",
    "LC_*",
    "TZ",
    "TMPDIR",
    "TEMP",
    "TMP",
    "PYTHON*",
    "VIRTUAL_ENV",
    "CONDA_*",
    "JUPYTER_*",
    "IPYTHONDIR",
    "MPLCONFIGDIR",
    "MPLBACKEND",
    "XDG_*",
    "GDAL_*",
    "PROJ_*",
    "CPL_*",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
    "LD_LIBRARY_PATH",
    "DYLD_*",
)
KERNEL_ENV_DENY = {"PYTHONSTARTUP"}
# Set per run by the factory; secrets can't use these names.
RUN_ENV = ("NOTEBOOK_RUN_ID", "STAC_API_URL", "STAC_API_KEY")


class SecretError(RuntimeError):
    pass


def _patterns() -> tuple[str, ...]:
    return (*KERNEL_ENV_ALLOW, *settings.NOTEBOOK_FACTORY["KERNEL_ENV_PASSTHROUGH"])


def passes_through(name: str) -> bool:
    """Whether the kernel inherits this worker variable."""
    return name not in KERNEL_ENV_DENY and any(fnmatchcase(name, p) for p in _patterns())


def base_env() -> dict[str, str]:
    """The worker variables a kernel inherits."""
    if settings.NOTEBOOK_FACTORY["KERNEL_ENV_INHERIT"]:
        return dict(os.environ)
    return {k: v for k, v in os.environ.items() if passes_through(k)}


def reserved_name(name: str) -> str:
    """Why `name` can't be a secret's name, or ""."""
    if name in RUN_ENV:
        return "it is set by the factory for every run"
    if passes_through(name):
        return "the kernel already inherits it from the worker"
    return ""


# -- secrets -----------------------------------------------------------------------
def _granted(notebook, names):
    return {s.name: s for s in notebook.secrets.filter(name__in=list(names))}


def secret_status(notebook, names) -> list[tuple[str, str]]:
    """[(name, "granted" | "not granted" | "unknown")] for the names a template declares."""
    from .models import TemplateSecret

    names = list(names or [])
    known = set(TemplateSecret.objects.filter(name__in=names).values_list("name", flat=True))
    granted = set(_granted(notebook, names)) if notebook.pk else set()
    return [(n, "granted" if n in granted else "not granted" if n in known else "unknown") for n in names]


def missing_secrets(notebook) -> list[str]:
    """Declared secrets a run of `notebook` can't get (value set on the worker can only be checked there)."""
    problems = []
    for name, state in secret_status(notebook, notebook.secret_names):
        if state == "unknown":
            problems.append(f"{name} (no such secret)")
        elif state == "not granted":
            problems.append(f"{name} (not granted to this template)")
    granted = _granted(notebook, notebook.secret_names)
    problems.extend(f"{n} (no value)" for n, s in granted.items() if not (s.value or s.from_env))
    return problems


def run_secrets(run) -> dict[str, str]:
    """{name: value} for the secrets the run's template version declares and is granted. Raises SecretError."""
    if run.notebook_id is None:
        return {}
    version = run.template_version
    names = list((version.secret_names if version else run.notebook.secret_names) or [])
    if not names:
        return {}
    granted = _granted(run.notebook, names)
    missing = [n for n in names if n not in granted]
    if missing:
        raise SecretError(f"Secrets not granted to this template: {', '.join(missing)}")
    values = {}
    for name in names:
        secret = granted[name]
        value = secret.resolve()
        if not value:
            where = f"worker variable {secret.from_env} is not set" if secret.from_env else "it has no value"
            raise SecretError(f"Secret {name} is empty ({where}).")
        values[name] = value
    return values
