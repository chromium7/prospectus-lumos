from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


class FreedomPlan(models.Model):
    """A user-owned, long-lived financial-freedom goal."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="freedom_plans")
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    is_archived = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("is_archived", "-updated_at")
        indexes = [models.Index(fields=("user", "is_archived", "-updated_at"), name="freedom_plan_library_idx")]

    def __str__(self) -> str:
        return self.name

    @property
    def latest_saved_scenario(self) -> FreedomScenario | None:
        """Return the highest immutable scenario version without maintaining a pointer."""

        return self.scenarios.filter(status=FreedomScenario.Status.SAVED).order_by("-version").first()


class FreedomScenario(models.Model):
    """An editable draft or an immutable saved version of a plan."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SAVED = "saved", "Saved"

    class SourceMode(models.TextChoices):
        MANUAL = "manual", "Manual"
        TRACKED_ACTUALS = "tracked_actuals", "Tracked actuals"

    class ResultStatus(models.TextChoices):
        ON_TRACK = "on_track", "On track"
        BEHIND = "behind", "Behind"
        UNREACHABLE = "unreachable", "Unreachable"
        COMPLETE = "complete", "Complete"

    plan = models.ForeignKey(FreedomPlan, on_delete=models.CASCADE, related_name="scenarios")
    version = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    based_on = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="derived_scenarios"
    )

    calculation_date = models.DateField()
    birth_date = models.DateField(null=True, blank=True)
    target_date = models.DateField()

    source_mode = models.CharField(max_length=20, choices=SourceMode.choices, default=SourceMode.MANUAL)
    source_period_start = models.DateField(null=True, blank=True)
    source_period_end = models.DateField(null=True, blank=True)
    source_month_count = models.PositiveSmallIntegerField(default=0)
    source_excluded_income_categories = models.JSONField(default=list, blank=True)
    source_excluded_expense_categories = models.JSONField(default=list, blank=True)

    current_monthly_income = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    current_monthly_expenses = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    current_monthly_investment = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    current_investable_assets = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    emergency_savings = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    desired_monthly_lifestyle = models.DecimalField(max_digits=20, decimal_places=2)
    post_freedom_monthly_income = models.DecimalField(max_digits=20, decimal_places=2, default=0)

    withdrawal_rate = models.DecimalField(max_digits=6, decimal_places=3, default=4)
    annual_return_rate = models.DecimalField(max_digits=6, decimal_places=3, default=7)
    annual_inflation_rate = models.DecimalField(max_digits=6, decimal_places=3, default=3)
    annual_income_growth_rate = models.DecimalField(max_digits=6, decimal_places=3, default=0)
    annual_contribution_growth_rate = models.DecimalField(max_digits=6, decimal_places=3, default=0)
    safety_buffer_rate = models.DecimalField(max_digits=6, decimal_places=3, default=10)
    include_emergency_reserve_in_target = models.BooleanField(default=False)

    base_freedom_number = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    total_target = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    required_monthly_investment = models.DecimalField(max_digits=20, decimal_places=2, null=True, blank=True)
    projected_achievement_date = models.DateField(null=True, blank=True)
    funding_gap = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    progress_percent = models.DecimalField(max_digits=8, decimal_places=3, default=0)
    result_status = models.CharField(max_length=20, choices=ResultStatus.choices, default=ResultStatus.BEHIND)
    projection_data = models.JSONField(default=dict, blank=True)
    calculation_version = models.PositiveSmallIntegerField(default=1)

    created_at = models.DateTimeField(auto_now_add=True)
    saved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-version", "-created_at")
        constraints = [
            models.UniqueConstraint(fields=("plan", "version"), name="freedom_scenario_plan_version_uniq"),
            models.UniqueConstraint(
                fields=("plan",), condition=Q(status="draft"), name="freedom_scenario_one_draft_uniq"
            ),
            models.CheckConstraint(
                condition=Q(target_date__gt=models.F("calculation_date")), name="freedom_scenario_target_after_calc"
            ),
            models.CheckConstraint(
                condition=(Q(status="draft", version=0) | Q(status="saved", version__gte=1)),
                name="freedom_scenario_version_matches_status",
            ),
            models.CheckConstraint(
                condition=(
                    Q(current_monthly_income__gte=0)
                    & Q(current_monthly_expenses__gte=0)
                    & Q(current_monthly_investment__gte=0)
                    & Q(current_investable_assets__gte=0)
                    & Q(emergency_savings__gte=0)
                    & Q(desired_monthly_lifestyle__gte=0)
                    & Q(post_freedom_monthly_income__gte=0)
                ),
                name="freedom_scenario_money_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(withdrawal_rate__gt=0, withdrawal_rate__lte=10),
                name="freedom_scenario_withdrawal_range",
            ),
            models.CheckConstraint(
                condition=Q(safety_buffer_rate__gte=0, safety_buffer_rate__lte=100),
                name="freedom_scenario_buffer_range",
            ),
            models.CheckConstraint(
                condition=(
                    Q(annual_return_rate__gte=-99, annual_return_rate__lte=100)
                    & Q(annual_inflation_rate__gte=-99, annual_inflation_rate__lte=100)
                    & Q(annual_income_growth_rate__gte=-99, annual_income_growth_rate__lte=100)
                    & Q(annual_contribution_growth_rate__gte=-99, annual_contribution_growth_rate__lte=100)
                ),
                name="freedom_scenario_annual_rate_range",
            ),
        ]
        indexes = [
            models.Index(fields=("plan", "status", "-version"), name="freedom_scenario_latest_idx"),
            models.Index(fields=("plan", "status", "-created_at"), name="freedom_scenario_status_idx"),
        ]

    def __str__(self) -> str:
        label = "draft" if self.status == self.Status.DRAFT else f"v{self.version}"
        return f"{self.plan.name} ({label})"

    def clean(self) -> None:
        """Validate portable invariants in addition to database constraints."""

        super().clean()
        if self.target_date and self.calculation_date and self.target_date <= self.calculation_date:
            raise ValidationError({"target_date": "Target date must be after the calculation date."})


class FinancialEvent(models.Model):
    """A dated life event belonging to one draft or saved scenario."""

    class Category(models.TextChoices):
        CAR = "car", "Car"
        HOME = "home", "Home"
        WEDDING = "wedding", "Wedding"
        EDUCATION = "education", "Education"
        BUSINESS = "business", "Business"
        MEDICAL = "medical", "Medical"
        FAMILY = "family", "Family support"
        CUSTOM = "custom", "Custom"

    class AmountBasis(models.TextChoices):
        TODAY = "today", "Today's money"
        EVENT_DATE = "event_date", "Event-date money"

    class FundingSource(models.TextChoices):
        INVESTMENT_PORTFOLIO = "investment_portfolio", "Investment portfolio"
        SEPARATE_SAVINGS = "separate_savings", "Separate savings"

    scenario = models.ForeignKey(FreedomScenario, on_delete=models.CASCADE, related_name="events")
    name = models.CharField(max_length=120)
    category = models.CharField(max_length=20, choices=Category.choices, default=Category.CUSTOM)
    event_date = models.DateField()
    one_time_amount = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    amount_basis = models.CharField(max_length=20, choices=AmountBasis.choices, default=AmountBasis.TODAY)
    recurring_monthly_amount = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    recurring_end_date = models.DateField(null=True, blank=True)
    funding_source = models.CharField(
        max_length=30, choices=FundingSource.choices, default=FundingSource.INVESTMENT_PORTFOLIO
    )
    sort_order = models.PositiveSmallIntegerField(default=0)
    notes = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("sort_order", "event_date", "id")
        constraints = [
            models.CheckConstraint(
                condition=Q(one_time_amount__gte=0, recurring_monthly_amount__gte=0),
                name="financial_event_money_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(one_time_amount__gt=0) | Q(recurring_monthly_amount__gt=0),
                name="financial_event_has_cost",
            ),
            models.CheckConstraint(
                condition=Q(recurring_end_date__isnull=True) | Q(recurring_end_date__gte=models.F("event_date")),
                name="financial_event_end_not_before_start",
            ),
            models.CheckConstraint(
                condition=Q(recurring_monthly_amount=0) | Q(recurring_end_date__isnull=False),
                name="financial_event_recurring_has_end",
            ),
        ]
        indexes = [models.Index(fields=("scenario", "sort_order"), name="financial_event_order_idx")]

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        """Validate event costs and inclusive recurring range."""

        super().clean()
        if self.one_time_amount <= 0 and self.recurring_monthly_amount <= 0:
            raise ValidationError("A life event must have a positive cost.")
        if self.recurring_end_date and self.recurring_end_date < self.event_date:
            raise ValidationError({"recurring_end_date": "The recurring end date cannot precede the event date."})
        if self.recurring_monthly_amount > 0 and not self.recurring_end_date:
            raise ValidationError({"recurring_end_date": "A recurring cost requires an end date."})
