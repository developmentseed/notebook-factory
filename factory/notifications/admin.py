from django.contrib import admin

from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("subject", "user", "email", "run", "sent_at", "read_at", "created_at")
    list_filter = ("sent_at", "read_at")
    raw_id_fields = ("run", "user")
