from __future__ import annotations

from django.contrib import admin
from django.http import HttpRequest

from .models import FinancialEvent, FreedomPlan, FreedomScenario


@admin.register(FreedomPlan)
class FreedomPlanAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "is_archived", "updated_at")
    list_filter = ("is_archived",)
    search_fields = ("name", "user__username")


class FinancialEventInline(admin.TabularInline):
    model = FinancialEvent
    extra = 0
    fields = ("name", "category", "event_date", "one_time_amount", "funding_source", "sort_order")

    def has_add_permission(self, request: HttpRequest, obj: FreedomScenario | None = None) -> bool:
        return bool(obj and obj.status == FreedomScenario.Status.DRAFT)

    def has_change_permission(self, request: HttpRequest, obj: FreedomScenario | None = None) -> bool:
        return bool(obj and obj.status == FreedomScenario.Status.DRAFT)

    def has_delete_permission(self, request: HttpRequest, obj: FreedomScenario | None = None) -> bool:
        return bool(obj and obj.status == FreedomScenario.Status.DRAFT)


@admin.register(FreedomScenario)
class FreedomScenarioAdmin(admin.ModelAdmin):
    list_display = ("plan", "version", "status", "result_status", "calculation_date", "saved_at")
    list_filter = ("status", "result_status", "calculation_version")
    search_fields = ("plan__name", "plan__user__username")
    inlines = (FinancialEventInline,)

    def get_readonly_fields(self, request: HttpRequest, obj: FreedomScenario | None = None) -> tuple[str, ...]:
        if obj and obj.status == FreedomScenario.Status.SAVED:
            return tuple(field.name for field in self.model._meta.fields)
        return (
            "base_freedom_number",
            "total_target",
            "required_monthly_investment",
            "projected_achievement_date",
            "funding_gap",
            "progress_percent",
            "result_status",
            "projection_data",
            "calculation_version",
            "saved_at",
        )

    def has_delete_permission(self, request: HttpRequest, obj: FreedomScenario | None = None) -> bool:
        return not obj or obj.status == FreedomScenario.Status.DRAFT


@admin.register(FinancialEvent)
class FinancialEventAdmin(admin.ModelAdmin):
    list_display = ("name", "scenario", "event_date", "funding_source", "sort_order")
    list_filter = ("category", "funding_source")

    def get_readonly_fields(self, request: HttpRequest, obj: FinancialEvent | None = None) -> tuple[str, ...]:
        if obj and obj.scenario.status == FreedomScenario.Status.SAVED:
            return tuple(field.name for field in self.model._meta.fields)
        return ()

    def has_delete_permission(self, request: HttpRequest, obj: FinancialEvent | None = None) -> bool:
        return not obj or obj.scenario.status == FreedomScenario.Status.DRAFT
