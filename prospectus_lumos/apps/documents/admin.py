from django.contrib import admin
from .models import Document


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    """Admin for processed documents"""

    list_display = [
        "user",
        "month_year",
        "google_sheet_name",
        "total_expenses",
        "total_income",
        "net_income",
        "expenses_count",
        "income_count",
        "processed_at",
    ]
    list_filter = ["year", "month", "source__source_type", "processed_at"]
    search_fields = ["user__username", "google_sheet_name", "google_sheet_id"]
    readonly_fields = ["processed_at", "updated_at", "net_income"]

    fieldsets = (
        (None, {"fields": ("user", "source", "month", "year")}),
        ("Google Sheet Information", {"fields": ("google_sheet_id", "google_sheet_name"), "classes": ("collapse",)}),
        (
            "File and Statistics",
            {"fields": ("csv_file", "total_expenses", "total_income", "expenses_count", "income_count")},
        ),
        ("Timestamps", {"fields": ("processed_at", "updated_at"), "classes": ("collapse",)}),
    )

    def month_year(self, obj):
        """Display month and year in a readable format"""
        return f"{obj.month_name} {obj.year}"

    month_year.short_description = "Month/Year"
    month_year.admin_order_field = ["year", "month"]
