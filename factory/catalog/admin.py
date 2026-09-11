from django.contrib import admin
from django.contrib.gis import admin as gis_admin

from .models import AdminArea, Country, Hazard


@admin.register(Hazard)
class HazardAdmin(admin.ModelAdmin):
    list_display = ("label", "key", "icon", "monty_codes", "order")
    ordering = ("order",)


@admin.register(Country)
class CountryAdmin(admin.ModelAdmin):
    list_display = ("iso3", "iso2", "name")
    search_fields = ("iso3", "name")


@admin.register(AdminArea)
class AdminAreaAdmin(gis_admin.GISModelAdmin):
    list_display = ("name", "code", "level", "country", "parent", "source", "area_km2")
    list_filter = ("source", "level", "country")
    search_fields = ("name", "code")
    raw_id_fields = ("parent",)
    readonly_fields = ("bbox", "centroid", "area_km2", "created_at", "updated_at")
