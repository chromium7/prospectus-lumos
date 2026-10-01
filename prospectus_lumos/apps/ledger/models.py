from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import User
from django.db import models
from django.db.models import F, Q
from django.utils import timezone


def first_of_month(value: date) -> date:
    """Return `value` normalised to the first day of its month."""

    return value.replace(day=1)


class ArchivableQuerySet(models.QuerySet):
    """Shared filters for the user-owned rows that are archived instead of deleted."""

    def for_user(self, user: User) -> ArchivableQuerySet:
        return self.filter(user=user)

    def active(self) -> ArchivableQuerySet:
        return self.filter(archived_at__isnull=True)

    def archived(self) -> ArchivableQuerySet:
        return self.filter(archived_at__isnull=False)


class FinancialAccount(models.Model):
    """A place money sits: cash, a bank account, an e-wallet, a card, or an investment."""

    class Type(models.TextChoices):
        CASH = "cash", "Cash"
        BANK = "bank", "Bank"
        E_WALLET = "e_wallet", "E-wallet"
        CREDIT = "credit", "Credit"
        INVESTMENT = "investment", "Investment"
        OTHER = "other", "Other"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="financial_accounts")
    name = models.CharField(max_length=120)
    type = models.CharField(max_length=20, choices=Type.choices, default=Type.CASH)
    opening_balance = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal("0.00"))
    opening_balance_date = models.DateField()
    include_in_budget = models.BooleanField(default=True)
    icon = models.CharField(max_length=40, blank=True)
    color = models.CharField(max_length=7, blank=True, help_text="Hex colour such as #2f6f4f")
    sort_order = models.PositiveIntegerField(default=0)
    archived_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ArchivableQuerySet.as_manager()

    class Meta:
        ordering = ("sort_order", "name", "id")
        indexes = [models.Index(fields=("user", "archived_at", "sort_order"), name="ledger_account_listing_idx")]

    def __str__(self) -> str:
        return self.name

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None

    def archive(self) -> None:
        """Hide the account from pickers while keeping it on historical transactions."""

        if self.archived_at is None:
            self.archived_at = timezone.now()
            self.save(update_fields=["archived_at", "updated_at"])

    def restore(self) -> None:
        """Make an archived account selectable again."""

        if self.archived_at is not None:
            self.archived_at = None
            self.save(update_fields=["archived_at", "updated_at"])


class Category(models.Model):
    """An income or expense bucket owned by one user."""

    class Type(models.TextChoices):
        INCOME = "income", "Income"
        EXPENSE = "expense", "Expense"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="ledger_categories")
    name = models.CharField(max_length=120)
    type = models.CharField(max_length=10, choices=Type.choices)
    icon = models.CharField(max_length=40, blank=True)
    color = models.CharField(max_length=7, blank=True, help_text="Hex colour such as #2f6f4f")
    sort_order = models.PositiveIntegerField(default=0)
    archived_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ArchivableQuerySet.as_manager()

    class Meta:
        ordering = ("type", "sort_order", "name", "id")
        verbose_name_plural = "categories"
        constraints = [
            models.UniqueConstraint(
                fields=("user", "type", "name"),
                condition=Q(archived_at__isnull=True),
                name="ledger_category_active_name_uniq",
            )
        ]
        indexes = [models.Index(fields=("user", "type", "archived_at"), name="ledger_category_listing_idx")]

    def __str__(self) -> str:
        return self.name

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None

    def archive(self) -> None:
        """Hide the category from pickers while keeping it on historical transactions."""

        if self.archived_at is None:
            self.archived_at = timezone.now()
            self.save(update_fields=["archived_at", "updated_at"])

    def restore(self) -> None:
        """Make an archived category selectable again."""

        if self.archived_at is not None:
            self.archived_at = None
            self.save(update_fields=["archived_at", "updated_at"])


class LedgerTransactionQuerySet(models.QuerySet):
    """Filters every ledger read shares: one user, and never the soft-deleted rows."""

    def for_user(self, user: User) -> LedgerTransactionQuerySet:
        return self.filter(user=user)

    def active(self) -> LedgerTransactionQuerySet:
        return self.filter(deleted_at__isnull=True)

    def deleted(self) -> LedgerTransactionQuerySet:
        return self.filter(deleted_at__isnull=False)

    def in_range(self, start: date, end: date) -> LedgerTransactionQuerySet:
        """Restrict to transactions that occurred within an inclusive date range."""

        return self.filter(occurred_on__gte=start, occurred_on__lte=end)


class LedgerTransaction(models.Model):
    """One directly entered income, expense, or transfer."""

    class Type(models.TextChoices):
        INCOME = "income", "Income"
        EXPENSE = "expense", "Expense"
        TRANSFER = "transfer", "Transfer"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="ledger_transactions")
    type = models.CharField(max_length=10, choices=Type.choices)
    amount = models.DecimalField(max_digits=15, decimal_places=2)
    occurred_on = models.DateField()
    account = models.ForeignKey(FinancialAccount, on_delete=models.PROTECT, related_name="transactions")
    transfer_account = models.ForeignKey(
        FinancialAccount,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="incoming_transfers",
        help_text="Destination account, transfers only",
    )
    category = models.ForeignKey(Category, on_delete=models.PROTECT, null=True, blank=True, related_name="transactions")
    payee = models.CharField(max_length=255, blank=True)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    objects = LedgerTransactionQuerySet.as_manager()

    class Meta:
        ordering = ("-occurred_on", "-id")
        indexes = [models.Index(fields=("user", "-occurred_on", "-id"), name="ledger_txn_user_recent_idx")]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="ledger_txn_amount_positive"),
            models.CheckConstraint(
                condition=(
                    Q(type__in=("income", "expense"), category__isnull=False, transfer_account__isnull=True)
                    | Q(type="transfer", category__isnull=True, transfer_account__isnull=False)
                ),
                name="ledger_txn_shape_matches_type",
            ),
            models.CheckConstraint(
                condition=Q(transfer_account__isnull=True) | ~Q(transfer_account=F("account")),
                name="ledger_txn_transfer_differs",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.get_type_display()} {self.amount} on {self.occurred_on}"

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def soft_delete(self) -> None:
        """Remove the transaction from every list and total without losing it."""

        if self.deleted_at is None:
            self.deleted_at = timezone.now()
            self.save(update_fields=["deleted_at", "updated_at"])

    def restore(self) -> None:
        """Put a soft-deleted transaction back into every list and total."""

        if self.deleted_at is not None:
            self.deleted_at = None
            self.save(update_fields=["deleted_at", "updated_at"])


class BudgetPeriod(models.Model):
    """One month of planned spending for one user."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="budget_periods")
    month = models.DateField(help_text="First day of the budgeted month")
    note = models.TextField(blank=True)
    is_closed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-month",)
        constraints = [models.UniqueConstraint(fields=("user", "month"), name="ledger_budget_period_month_uniq")]

    def __str__(self) -> str:
        return self.month.strftime("%Y-%m")

    def save(self, *args: object, **kwargs: object) -> None:
        """Normalise the month to its first day so one month cannot be stored twice."""

        if self.month is not None:
            self.month = first_of_month(self.month)
        super().save(*args, **kwargs)  # type: ignore[arg-type]


class BudgetAllocation(models.Model):
    """The amount one expense category is allowed in one budgeted month."""

    period = models.ForeignKey(BudgetPeriod, on_delete=models.CASCADE, related_name="allocations")
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="allocations")
    planned_amount = models.DecimalField(max_digits=15, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("category__sort_order", "category__name", "id")
        constraints = [
            models.UniqueConstraint(fields=("period", "category"), name="ledger_allocation_category_uniq"),
            models.CheckConstraint(condition=Q(planned_amount__gte=0), name="ledger_allocation_amount_not_negative"),
        ]

    def __str__(self) -> str:
        return f"{self.category.name} {self.planned_amount}"
