import logging

from celery import shared_task

from .poller import poll

log = logging.getLogger(__name__)


@shared_task(name="factory.events.tasks.poll_catalogs")
def poll_catalogs():
    summary = poll()
    log.info("poll: %s", summary)
    return summary
