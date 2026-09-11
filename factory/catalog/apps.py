from django.apps import AppConfig


class CatalogConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "factory.catalog"
    verbose_name = "Catalog (areas & hazards)"
