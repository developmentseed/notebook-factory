"""
Keep the secrets given to a kernel out of what gets published.

After execution (and when a run fails), every secret value the kernel received
(granted template secrets and STAC_API_KEY) is replaced with `[redacted:NAME]`
in the executed notebook, the text files in outputs/, the run log and the run's
error. This is a safety net for a stray print(); values that were encoded or
transformed (base64, URL-quoted…) are not recognised.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..models import NotebookRun
from .context import RunContext

MIN_LENGTH = 6  # shorter values would match ordinary text
MAX_FILE_BYTES = 20 * 1024 * 1024


def _pairs(secrets: dict[str, str]) -> list[tuple[str, str]]:
    """(text to find, replacement), longest first; also the JSON-escaped form used inside .ipynb files."""
    pairs = []
    for name, value in secrets.items():
        if not value or len(value) < MIN_LENGTH:
            continue
        mark = f"[redacted:{name}]"
        pairs.append((value, mark))
        escaped = json.dumps(value)[1:-1]
        if escaped != value:
            pairs.append((escaped, mark))
    return sorted(pairs, key=lambda p: -len(p[0]))


def redact_text(text: str, secrets: dict[str, str]) -> tuple[str, set[str]]:
    found = set()
    for needle, mark in _pairs(secrets):
        if needle in text:
            text = text.replace(needle, mark)
            found.add(mark[len("[redacted:") : -1])
    return text, found


def _redact_file(path: Path, secrets) -> set[str]:
    if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
        return set()
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return set()  # binary files are not scanned
    new, found = redact_text(text, secrets)
    if found:
        path.write_text(new, encoding="utf-8")
    return found


def redact_log(run: NotebookRun, secrets: dict[str, str]) -> set[str]:
    found = set()
    if not _pairs(secrets):
        return found
    for entry in run.log_entries.all():
        new, hits = redact_text(entry.message, secrets)
        if hits:
            entry.message = new
            entry.save(update_fields=["message"])
            found |= hits
    if run.error:
        run.error, hits = redact_text(run.error, secrets)
        if hits:
            run.save(update_fields=["error", "updated_at"])
            found |= hits
    return found


def redact_run(run: NotebookRun, ctx: RunContext) -> set[str]:
    """Redact the executed notebook, outputs and log; returns the names of the secrets found."""
    secrets = ctx.secrets
    if not _pairs(secrets):
        return set()
    found = set()
    if ctx.executed_path is not None:
        found |= _redact_file(ctx.executed_path, secrets)
    for path in sorted(ctx.outputs_dir.rglob("*")):
        found |= _redact_file(path, secrets)
    found |= redact_log(run, secrets)
    if found:
        run.log(
            "redacted secret value(s) printed or written by the notebook: "
            + ", ".join(sorted(found))
            + ". Don't print secrets; they would be published.",
            level="warning",
        )
    return found
