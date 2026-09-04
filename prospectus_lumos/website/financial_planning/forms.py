from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from django import forms
from django.utils import timezone

from prospectus_lumos.apps.financial_planning.models import FreedomPlan, FreedomScenario

MONEY_FIELDS = (
    "current_monthly_income",
    "current_monthly_expenses",
    "current_monthly_investment",
    "current_investable_assets",
    "emergency_savings",
    "desired_monthly_lifestyle",
    "post_freedom_monthly_income",
)
RATE_FIELDS = (
    "withdrawal_rate",
    "annual_return_rate",
    "annual_inflation_rate",
    "annual_income_growth_rate",
    "annual_contribution_growth_rate",
    "safety_buffer_rate",
)


class FreedomPlanForm(forms.ModelForm):
    """Create or rename a financial-freedom plan."""

    class Meta:
        model = FreedomPlan
        fields = ("name", "description")
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "Freedom at 45"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 2}),
        }

    def clean_name(self) -> str:
        name = self.cleaned_data["name"].strip()
        if not name:
            raise forms.ValidationError("Enter a plan name.", code="required")
        return name


class FreedomScenarioForm(forms.ModelForm):
    """Parse and validate the manual financial-freedom builder inputs."""

    MONEY_FIELDS = MONEY_FIELDS
    RATE_FIELDS = RATE_FIELDS

    class Meta:
        model = FreedomScenario
        fields = (
            "calculation_date",
            "birth_date",
            "target_date",
            *MONEY_FIELDS,
            *RATE_FIELDS,
            "include_emergency_reserve_in_target",
        )
        widgets = {
            "calculation_date": forms.HiddenInput(),
            "birth_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "target_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "include_emergency_reserve_in_target": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }
        help_texts = {
            "desired_monthly_lifestyle": "Your estimated monthly lifestyle in today's rupiah.",
            "current_investable_assets": "Investments available for this goal; emergency cash is separate by default.",
            "withdrawal_rate": "Planning assumption, expressed as a percentage (for example, 4).",
            "annual_return_rate": "Nominal annual estimate before inflation.",
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        for field_name in (*self.MONEY_FIELDS, *self.RATE_FIELDS):
            self.fields[field_name].widget.attrs.update({"class": "form-control", "inputmode": "decimal"})
        self.fields["calculation_date"].initial = self.initial.get("calculation_date", timezone.localdate())

    def clean_birth_date(self) -> date | None:
        birth_date = self.cleaned_data.get("birth_date")
        if birth_date and birth_date > timezone.localdate():
            raise forms.ValidationError("Birth date cannot be in the future.", code="future_birth_date")
        return birth_date

    def clean(self) -> dict[str, Any]:
        cleaned_data = super().clean()
        if self.errors:
            return cleaned_data
        calculation_date = cleaned_data["calculation_date"]
        target_date = cleaned_data["target_date"]
        if target_date <= calculation_date:
            self.add_error(
                "target_date",
                forms.ValidationError("Target date must be after the calculation date.", code="invalid_target_date"),
            )
        for field_name in self.MONEY_FIELDS:
            value: Decimal = cleaned_data[field_name]
            if value < 0:
                self.add_error(field_name, forms.ValidationError("Amount cannot be negative.", code="negative_money"))
        withdrawal_rate: Decimal = cleaned_data["withdrawal_rate"]
        if not Decimal("0") < withdrawal_rate <= Decimal("10"):
            self.add_error(
                "withdrawal_rate",
                forms.ValidationError("Withdrawal rate must be above 0% and no more than 10%.", code="rate_range"),
            )
        safety_buffer_rate: Decimal = cleaned_data["safety_buffer_rate"]
        if not Decimal("0") <= safety_buffer_rate <= Decimal("100"):
            self.add_error(
                "safety_buffer_rate",
                forms.ValidationError("Safety buffer must be between 0% and 100%.", code="rate_range"),
            )
        for field_name in (
            "annual_return_rate",
            "annual_inflation_rate",
            "annual_income_growth_rate",
            "annual_contribution_growth_rate",
        ):
            rate: Decimal = cleaned_data[field_name]
            if not Decimal("-99") <= rate <= Decimal("100"):
                self.add_error(
                    field_name,
                    forms.ValidationError("Annual rate must be between -99% and 100%.", code="rate_range"),
                )
        return cleaned_data

    @classmethod
    def initial_values(cls) -> dict[str, object]:
        """Return useful defaults for a manual-only user."""

        today = timezone.localdate()
        try:
            target = today.replace(year=today.year + 20, day=1)
        except ValueError:
            target = date(today.year + 20, today.month, 1)
        return {
            "calculation_date": today,
            "target_date": target,
            "current_monthly_income": Decimal("0"),
            "current_monthly_expenses": Decimal("0"),
            "current_monthly_investment": Decimal("0"),
            "current_investable_assets": Decimal("0"),
            "emergency_savings": Decimal("0"),
            "desired_monthly_lifestyle": Decimal("20000000"),
            "post_freedom_monthly_income": Decimal("0"),
            "withdrawal_rate": Decimal("4"),
            "annual_return_rate": Decimal("7"),
            "annual_inflation_rate": Decimal("3"),
            "annual_income_growth_rate": Decimal("0"),
            "annual_contribution_growth_rate": Decimal("0"),
            "safety_buffer_rate": Decimal("10"),
            "include_emergency_reserve_in_target": False,
        }

    def scenario_values(self) -> dict[str, Any]:
        """Return only persisted, server-validated input fields."""

        return {field_name: self.cleaned_data[field_name] for field_name in self.Meta.fields}
