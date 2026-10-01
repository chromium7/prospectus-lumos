from django.contrib import admin

from .models import BudgetAllocation, BudgetPeriod, Category, FinancialAccount, LedgerTransaction


@admin.register(FinancialAccount)
class FinancialAccountAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "type", "opening_balance", "include_in_budget", "archived_at")
    list_filter = ("type", "include_in_budget")
    search_fields = ("name", "user__username")
    raw_id_fields = ("user",)


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "type", "sort_order", "archived_at")
    list_filter = ("type",)
    search_fields = ("name", "user__username")
    raw_id_fields = ("user",)


@admin.register(LedgerTransaction)
class LedgerTransactionAdmin(admin.ModelAdmin):
    list_display = ("occurred_on", "user", "type", "amount", "account", "category", "payee", "deleted_at")
    list_filter = ("type",)
    search_fields = ("payee", "note", "user__username")
    date_hierarchy = "occurred_on"
    raw_id_fields = ("user", "account", "transfer_account", "category")


class BudgetAllocationInline(admin.TabularInline):
    model = BudgetAllocation
    extra = 0
    raw_id_fields = ("category",)


@admin.register(BudgetPeriod)
class BudgetPeriodAdmin(admin.ModelAdmin):
    list_display = ("month", "user", "is_closed")
    list_filter = ("is_closed",)
    search_fields = ("user__username",)
    raw_id_fields = ("user",)
    inlines = (BudgetAllocationInline,)
