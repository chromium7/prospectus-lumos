from django.contrib import admin
from .models import Transaction


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    """Admin for individual transactions"""

    list_display = ["document", "transaction_type", "date", "description", "amount", "category"]
    list_filter = ["transaction_type", "document__year", "document__month", "category"]
    search_fields = ["description", "category", "document__user__username"]
    readonly_fields = ["created_at"]

    fieldsets = (
        (None, {"fields": ("document", "transaction_type", "date", "amount", "description", "category")}),
        ("Timestamps", {"fields": ("created_at",), "classes": ("collapse",)}),
    )

    def get_queryset(self, request):
        """Optimize queries by selecting related objects"""
        qs = super().get_queryset(request)
        return qs.select_related("document", "document__user")
