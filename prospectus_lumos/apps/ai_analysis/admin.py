from django.contrib import admin
from .models import AISettings


@admin.register(AISettings)
class AISettingsAdmin(admin.ModelAdmin):
    list_display = ["user", "model", "created_at", "updated_at"]
    list_filter = ["model", "created_at"]
    search_fields = ["user__username"]
    readonly_fields = ["created_at", "updated_at"]

    fieldsets = (
        (None, {"fields": ("user", "api_token", "model")}),
        ("Timestamps", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )
