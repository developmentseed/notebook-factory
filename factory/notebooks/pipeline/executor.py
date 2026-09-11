"""Execute a template with papermill, streaming cell progress into the run record."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

import nbformat
import papermill as pm
from django.conf import settings

from ..models import NotebookRun
from .context import RunContext

log = logging.getLogger(__name__)

_CELL_RE = re.compile(r"^(Executing|Ending) Cell (\d+)")


class ExecutionError(RuntimeError):
    pass


class _ProgressHandler(logging.Handler):
    """
    Captures papermill's per-cell log lines into RunLogEntry + run.progress.

    papermill logs from inside nbclient's asyncio loop. The ORM calls here are
    plain blocking calls on the worker thread (nothing interleaves with them),
    so `execute()` lifts Django's async guard for the duration of the run.
    """

    def __init__(self, run: NotebookRun, total_cells: int):
        super().__init__(level=logging.INFO)
        self.run = run
        self.total = total_cells
        self._last_cell = 0

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage().rstrip("-").strip()
            m = _CELL_RE.match(msg)
            if m and m.group(1) == "Executing":
                cell = int(m.group(2))
                if cell != self._last_cell:
                    self._last_cell = cell
                    self.run.progress = {
                        **(self.run.progress or {}),
                        "cell": cell,
                        "total": self.total,
                        "message": f"papermill · cell {cell} / {self.total}",
                    }
                    self.run.save(update_fields=["progress", "updated_at"])
                    self.run.log(f"papermill: executing cell {cell}/{self.total}")
            elif m:
                return
            elif msg:
                for line in msg.splitlines()[:20]:  # cell output lines (log_output=True); keep them short
                    if line.strip():
                        self.run.log(line[:500], level="output")
        except Exception:  # pragma: no cover - never let logging break execution
            log.exception("progress handler failed")


def execute(run: NotebookRun, ctx: RunContext, parameters: dict) -> Path:
    cfg = settings.NOTEBOOK_FACTORY
    src = ctx.template_path
    assert src is not None
    nb = nbformat.read(src, as_version=4)
    total = len(nb.cells) + 1  # + injected parameters cell
    kernel = nb.metadata.get("kernelspec", {}).get("name") or cfg["KERNEL_NAME"]
    ctx.executed_path = ctx.workdir / "notebook.ipynb"

    env_extra = {
        "MONTANDON_API_TOKEN": settings.MONTANDON["API_TOKEN"],
        "MONTANDON_STAC_URL": settings.MONTANDON["STAC_URL"],
        "NOTEBOOK_RUN_ID": str(run.id),
        "PYTHONUNBUFFERED": "1",
        "DJANGO_ALLOW_ASYNC_UNSAFE": "1",  # see _ProgressHandler
    }
    old_env = {k: os.environ.get(k) for k in env_extra}
    os.environ.update({k: v for k, v in env_extra.items() if v is not None})

    pm_logger = logging.getLogger("papermill")
    handler = _ProgressHandler(run, total)
    pm_logger.addHandler(handler)
    try:
        pm.execute_notebook(
            str(src),
            str(ctx.executed_path),
            parameters=parameters,
            kernel_name=kernel,
            cwd=str(src.parent),
            log_output=True,
            progress_bar=False,
            request_save_on_cell_execute=True,
            execution_timeout=cfg["CELL_TIMEOUT"],
            start_timeout=120,
        )
    except pm.PapermillExecutionError as exc:
        raise ExecutionError(
            f"Cell {exc.exec_count} failed: {exc.ename}: {exc.evalue}\n\n{exc.traceback[-1] if exc.traceback else ''}"
        ) from exc
    finally:
        pm_logger.removeHandler(handler)
        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return ctx.executed_path
