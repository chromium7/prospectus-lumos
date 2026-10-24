from __future__ import annotations

from typing import Any

from django.conf import settings
from django.contrib.auth.models import User
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q, QuerySet

from prospectus_lumos.core.categories import category_key


class CategoryBudget(models.Model):
    """A user's expense limit for one text category and calendar month."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="category_budgets")
    year = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(9999)])
    month = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(12)])
    category = models.CharField(max_length=100, blank=True, default="Uncategorized")
    amount = models.DecimalField(max_digits=15, decimal_places=2, validators=[MinValueValidator(0)])
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("user", "year", "month", "category"), name="category_budget_month_uniq"),
            models.CheckConstraint(condition=Q(year__gte=1, year__lte=9999), name="category_budget_year_valid"),
            models.CheckConstraint(condition=Q(month__gte=1, month__lte=12), name="category_budget_month_valid"),
            models.CheckConstraint(condition=Q(amount__gte=0), name="category_budget_amount_nonnegative"),
        ]

    def clean(self) -> None:
        """Normalize the category before model uniqueness validation."""

        super().clean()
        self.category = category_key(self.category)

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist the same category key used during validation."""

        self.category = category_key(self.category)
        super().save(*args, **kwargs)


class Transaction(models.Model):
    """Individual imported or manually entered transaction records."""

    class TransactionType(models.TextChoices):
        EXPENSE = "expense", "Expense"
        INCOME = "income", "Income"

    class Origin(models.TextChoices):
        MANUAL = "manual", "Manual"
        GOOGLE_SHEETS = "google_sheets", "Google Sheets"
        DIRECT_UPLOAD = "direct_upload", "Direct Upload"

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="transactions",
        blank=True,
        null=True,
    )
    document = models.ForeignKey(
        "documents.Document",
        on_delete=models.CASCADE,
        related_name="transactions",
        blank=True,
        null=True,
    )
    transaction_type = models.CharField(max_length=10, choices=TransactionType.choices)
    date = models.CharField(max_length=50, help_text="Date as string from original sheet")
    amount = models.DecimalField(max_digits=15, decimal_places=2)
    description = models.CharField(max_length=500)
    category = models.CharField(max_length=100, blank=True)
    origin = models.CharField(max_length=20, choices=Origin.choices, default=Origin.MANUAL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["date", "id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(user__isnull=False) | Q(document__isnull=False),
                name="transaction_has_owner",
            )
        ]

    @classmethod
    def for_user(cls, user: User) -> QuerySet[Transaction]:
        """Return imported and manually entered transactions owned by a user."""

        return cls.objects.filter(Q(user=user) | Q(document__user=user)).distinct()

    def __str__(self) -> str:
        return f"{self.transaction_type} - {self.description} - {self.amount}"
