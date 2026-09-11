from django.conf import settings
from django.db import models


class Notification(models.Model):
    """A message about a finished run, delivered by email and/or shown in-app."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    email = models.EmailField(blank=True)
    run = models.ForeignKey("notebooks.NotebookRun", on_delete=models.CASCADE, related_name="notifications")
    subject = models.CharField(max_length=200)
    body = models.TextField()
    sent_at = models.DateTimeField(null=True, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.subject

    @property
    def is_read(self) -> bool:
        return self.read_at is not None
