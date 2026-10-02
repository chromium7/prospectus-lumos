from django.contrib import admin
from django.http import HttpRequest
from django.db.models import QuerySet

from .models import Transaction


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    """Admin for individual transactions"""

    list_display = ["user", "document", "transaction_type", "date", "description", "amount", "category"]
    list_filter = ["transaction_type", "document__year", "document__month", "category"]
    search_fields = ["description", "category", "user__username", "document__user__username"]
    readonly_fields = ["created_at"]

    fieldsets = (
        (None, {"fields": ("user", "document", "transaction_type", "date", "amount", "description", "category")}),
        ("Timestamps", {"fields": ("created_at",), "classes": ("collapse",)}),
    )

    def get_queryset(self, request: HttpRequest) -> QuerySet[Transaction]:
        """Optimize queries by selecting related objects"""
        qs = super().get_queryset(request)
        return qs.select_related("user", "document", "document__user")
