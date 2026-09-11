from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.utils import timezone

from factory.notebooks.models import NotebookRun, RunStatus

from .models import Notification

log = logging.getLogger(__name__)


def _absolute(path: str) -> str:
    return settings.PUBLIC_BASE_URL.rstrip("/") + path


def notify_run_finished(run: NotebookRun) -> list[Notification]:
    """Create in-app notifications and send emails to everyone interested in the run."""
    if run.status not in (RunStatus.PUBLISHED, RunStatus.FAILED):
        return []
    User = get_user_model()
    recipients: dict[str, object | None] = {}
    if run.triggered_by_id:
        recipients[run.triggered_by.email or f"user-{run.triggered_by_id}"] = run.triggered_by
    for email in run.notify_emails or []:
        recipients.setdefault(email, User.objects.filter(email__iexact=email).first())

    if run.status == RunStatus.PUBLISHED:
        subject = f"[{settings.SITE_NAME}] Analysis ready: {run.title}"
        body = (
            f"Your analysis has been published.\n\n"
            f"{run.title}\n{run.subtitle}\n{run.parameter_summary()}\n\n"
            f"Open the result: {_absolute(run.output_url)}\n"
            f"Run details: {_absolute(run.get_absolute_url())}\n"
        )
    else:
        subject = f"[{settings.SITE_NAME}] Analysis failed: {run.title}"
        body = f"The run failed during {run.progress.get('failed_stage', 'execution')}.\n\n{run.error[:1500]}\n\nRun details: {_absolute(run.get_absolute_url())}\n"

    created = []
    for email, user in recipients.items():
        n = Notification.objects.create(
            user=user, email=email if "@" in email else "", run=run, subject=subject, body=body
        )
        if "@" in email:
            try:
                send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [email], fail_silently=False)
                n.sent_at = timezone.now()
            except Exception as exc:  # noqa: BLE001
                log.warning("email to %s failed: %s", email, exc)
                n.error = str(exc)
            n.save(update_fields=["sent_at", "error"])
        created.append(n)
    return created
