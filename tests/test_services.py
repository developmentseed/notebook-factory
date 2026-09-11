import pytest

from factory.catalog.models import AdminArea
from factory.notebooks.models import NotebookRun, RunStatus, TriggerKind
from factory.notebooks.services import RunRequestError, cancel_run, create_batch, create_run, retry_run


def test_create_run_validates_and_resolves_hazard(template, nepal, user):
    run = create_run(
        template, {"hazard": "earthquake"}, area=nepal["a1"], user=user, trigger=TriggerKind.MANUAL
    )
    assert run.status == RunStatus.QUEUED
    assert run.parameters["threshold"] == 0.5  # default filled
    assert run.hazard.key == "earthquake"
    assert run.notify_emails == ["ana@example.org"]
    assert run.log_entries.count() == 1


def test_create_run_requires_area(template):
    with pytest.raises(RunRequestError):
        create_run(template, {"hazard": "flood"})


def test_create_run_respects_area_levels(template, nepal):
    template.area_levels = [2]
    template.save()
    with pytest.raises(RunRequestError):
        create_run(template, {"hazard": "flood"}, area=nepal["a1"])


def test_batch_fans_out(template, nepal, user):
    areas = AdminArea.objects.filter(country_id="NPL", level=2)
    batch = create_batch(template, {"hazard": "flood"}, areas, user=user)
    assert batch.runs.count() == 4
    assert batch.status_counts()["queued"] == 4


def test_cancel_and_retry(template, nepal, user):
    run = create_run(template, {"hazard": "flood"}, area=nepal["a1"], user=user)
    assert cancel_run(run) is True
    assert run.status == RunStatus.CANCELLED and run.finished_at
    assert cancel_run(run) is False
    new = retry_run(run)
    assert new.pk != run.pk and new.parameters == run.parameters and new.area == run.area
    assert NotebookRun.objects.count() == 2


def test_stages_view_model(template, nepal):
    run = create_run(template, {"hazard": "flood"}, area=nepal["a1"])
    run.set_status(RunStatus.EXECUTING, "cell 3", cell=3, total=10)
    states = {s["key"]: s["state"] for s in run.stages()}
    assert (
        states["queued"] == "done"
        and states["fetching"] == "done"
        and states["executing"] == "active"
        and states["rendering"] == "pending"
    )
    run.set_status(RunStatus.FAILED, failed_stage="executing")
    assert {s["key"]: s["state"] for s in run.stages()}["executing"] == "failed"
