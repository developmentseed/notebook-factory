from django.apps import AppConfig


class NotebooksConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "factory.notebooks"
    verbose_name = "Notebooks (templates & runs)"
