from django.db import models


class Transaction(models.Model):
    """Individual transaction records extracted from CSV files"""

    TRANSACTION_TYPES = [
        ("expense", "Expense"),
        ("income", "Income"),
    ]

    document = models.ForeignKey("documents.Document", on_delete=models.CASCADE, related_name="transactions")
    transaction_type = models.CharField(max_length=10, choices=TRANSACTION_TYPES)
    date = models.CharField(max_length=50, help_text="Date as string from original sheet")
    amount = models.DecimalField(max_digits=15, decimal_places=2)
    description = models.CharField(max_length=500)
    category = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["date", "id"]

    def __str__(self):
        return f"{self.transaction_type} - {self.description} - {self.amount}"
