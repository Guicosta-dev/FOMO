from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import Event, User


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (("FOMO", {"fields": ("role",)}),)
    add_fieldsets = UserAdmin.add_fieldsets + (("FOMO", {"fields": ("email", "role")}),)
    list_display = UserAdmin.list_display + ("role",)
    list_filter = UserAdmin.list_filter + ("role",)


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ("title", "producer", "city", "starts_at", "status")
    list_filter = ("status", "city")
    search_fields = ("title", "producer__username", "city")
    readonly_fields = ("slug", "published_at", "created_at", "updated_at")


