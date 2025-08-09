from django.db import models
from django.contrib.auth.models import User
from django.core.validators import FileExtensionValidator


class Document(models.Model):
    """Store processed CSV documents for each month/year"""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="documents")
    source = models.ForeignKey("accounts.DocumentSource", on_delete=models.CASCADE, related_name="documents")
    google_sheet_id = models.CharField(
        max_length=255, blank=True, help_text="Google Sheet ID for tracking and sync purposes"
    )
    google_sheet_name = models.CharField(max_length=255, blank=True, help_text="Original Google Sheet name")
    month = models.PositiveSmallIntegerField(help_text="Month (1-12)")
    year = models.PositiveIntegerField(help_text="Year (e.g., 2025)")
    csv_file = models.FileField(
        upload_to="documents/csv/",
        validators=[FileExtensionValidator(allowed_extensions=["csv"])],
        help_text="Processed CSV file containing expenses and income",
    )
    total_expenses = models.DecimalField(
        max_digits=15, decimal_places=2, default=0, help_text="Total expenses for this month"
    )
    total_income = models.DecimalField(
        max_digits=15, decimal_places=2, default=0, help_text="Total income for this month"
    )
    expenses_count = models.PositiveIntegerField(default=0, help_text="Number of expense entries")
    income_count = models.PositiveIntegerField(default=0, help_text="Number of income entries")
    processed_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ["user", "month", "year"]
        ordering = ["-year", "-month"]

    def __str__(self):
        return f"{self.user.username} - {self.year}/{self.month:02d}"

    @property
    def month_name(self):
        """Return the month name"""
        months = [
            "",
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ]
        return months[self.month] if 1 <= self.month <= 12 else ""

    @property
    def net_income(self):
        """Calculate net income (income - expenses)"""
        return self.total_income - self.total_expenses
