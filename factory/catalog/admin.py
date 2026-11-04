from django.contrib import admin
from django.contrib.gis import admin as gis_admin

from .models import AdminArea, Country, Facet, FacetOption


class FacetOptionInline(admin.TabularInline):
    model = FacetOption
    extra = 1
    fields = ("key", "label", "icon", "color", "match_codes", "order")
    prepopulated_fields = {"key": ("label",)}


@admin.register(Facet)
class FacetAdmin(admin.ModelAdmin):
    list_display = ("label", "key", "multiple", "stac_property", "show_in_browse", "order")
    list_editable = ("order",)
    prepopulated_fields = {"key": ("label",)}
    inlines = [FacetOptionInline]


@admin.register(FacetOption)
class FacetOptionAdmin(admin.ModelAdmin):
    list_display = ("label", "key", "facet", "icon", "match_codes", "order")
    list_filter = ("facet",)
    search_fields = ("label", "key")


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
