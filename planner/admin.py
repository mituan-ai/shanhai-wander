from django.contrib import admin

from .models import Bookmark, RouteTemplate, Trip


@admin.register(RouteTemplate)
class RouteTemplateAdmin(admin.ModelAdmin):
    list_display = ("title", "region", "category", "days", "featured")
    list_filter = ("featured", "region", "category")
    search_fields = ("title", "subtitle", "description")
    prepopulated_fields = {"slug": ("title",)}


@admin.register(Trip)
class TripAdmin(admin.ModelAdmin):
    list_display = ("title", "owner", "travel_mode", "days", "is_public", "updated_at")
    list_filter = ("travel_mode", "is_public", "status")
    search_fields = ("title", "owner__username")
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(Bookmark)
class BookmarkAdmin(admin.ModelAdmin):
    list_display = ("user", "route", "created_at")
    search_fields = ("user__username", "route__title")
