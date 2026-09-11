from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from django.conf import settings


@dataclass
class RunContext:
    """Filesystem layout of one run's work directory."""

    run_id: str
    workdir: Path
    template_path: Path | None = None
    executed_path: Path | None = None
    html_dir: Path | None = None
    injected: dict = field(default_factory=dict)

    @classmethod
    def create(cls, run_id: str) -> RunContext:
        base = Path(settings.NOTEBOOK_FACTORY["WORK_DIR"])
        workdir = base / str(run_id)
        for sub in ("inputs", "outputs", "build"):
            (workdir / sub).mkdir(parents=True, exist_ok=True)
        return cls(run_id=str(run_id), workdir=workdir)

    @property
    def inputs_dir(self) -> Path:
        return self.workdir / "inputs"

    @property
    def outputs_dir(self) -> Path:
        return self.workdir / "outputs"

    @property
    def build_dir(self) -> Path:
        return self.workdir / "build"
