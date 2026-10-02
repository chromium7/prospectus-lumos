from __future__ import annotations

from django.contrib.auth.models import User
from django.db import models
from django.db.models import Q, QuerySet


class Transaction(models.Model):
    """Individual transaction records extracted from CSV files"""

    class TransactionType(models.TextChoices):
        EXPENSE = "expense", "Expense"
        INCOME = "income", "Income"

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
