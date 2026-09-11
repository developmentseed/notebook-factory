from django.conf import settings


def site(request):
    unread = 0
    if request.user.is_authenticated:
        unread = request.user.notifications.filter(read_at__isnull=True).count()
    return {
        "SITE_NAME": settings.SITE_NAME,
        "unread_notifications": unread,
        "eager_mode": settings.CELERY_TASK_ALWAYS_EAGER,
    }
