from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, cast

from django import forms
from django.forms import BaseInlineFormSet, inlineformset_factory
from django.utils import timezone

from prospectus_lumos.apps.financial_planning.models import FinancialEvent, FreedomPlan, FreedomScenario
from prospectus_lumos.apps.financial_planning.services import ActualsSnapshotService

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
MAX_EVENT_FORMS = 50

EVENT_PRESETS: dict[str, dict[str, object]] = {
    "car": {
        "label": "Car purchase",
        "name": "Car purchase",
        "category": FinancialEvent.Category.CAR,
        "one_time_amount": "300000000",
        "recurring_monthly_amount": "5000000",
        "duration_months": 36,
        "amount_basis": FinancialEvent.AmountBasis.TODAY,
        "funding_source": FinancialEvent.FundingSource.INVESTMENT_PORTFOLIO,
    },
    "home": {
        "label": "Home deposit",
        "name": "Home deposit",
        "category": FinancialEvent.Category.HOME,
        "one_time_amount": "500000000",
        "recurring_monthly_amount": "0",
        "duration_months": 0,
        "amount_basis": FinancialEvent.AmountBasis.TODAY,
        "funding_source": FinancialEvent.FundingSource.SEPARATE_SAVINGS,
    },
    "wedding": {
        "label": "Wedding",
        "name": "Wedding",
        "category": FinancialEvent.Category.WEDDING,
        "one_time_amount": "200000000",
        "recurring_monthly_amount": "0",
        "duration_months": 0,
        "amount_basis": FinancialEvent.AmountBasis.TODAY,
        "funding_source": FinancialEvent.FundingSource.INVESTMENT_PORTFOLIO,
    },
    "education": {
        "label": "Education",
        "name": "Education",
        "category": FinancialEvent.Category.EDUCATION,
        "one_time_amount": "250000000",
        "recurring_monthly_amount": "0",
        "duration_months": 0,
        "amount_basis": FinancialEvent.AmountBasis.TODAY,
        "funding_source": FinancialEvent.FundingSource.INVESTMENT_PORTFOLIO,
    },
    "business": {
        "label": "Business",
        "name": "Business",
        "category": FinancialEvent.Category.BUSINESS,
        "one_time_amount": "300000000",
        "recurring_monthly_amount": "0",
        "duration_months": 0,
        "amount_basis": FinancialEvent.AmountBasis.TODAY,
        "funding_source": FinancialEvent.FundingSource.INVESTMENT_PORTFOLIO,
    },
    "medical": {
        "label": "Medical reserve",
        "name": "Medical reserve",
        "category": FinancialEvent.Category.MEDICAL,
        "one_time_amount": "100000000",
        "recurring_monthly_amount": "0",
        "duration_months": 0,
        "amount_basis": FinancialEvent.AmountBasis.TODAY,
        "funding_source": FinancialEvent.FundingSource.SEPARATE_SAVINGS,
    },
    "family": {
        "label": "Family support",
        "name": "Family support",
        "category": FinancialEvent.Category.FAMILY,
        "one_time_amount": "0",
        "recurring_monthly_amount": "5000000",
        "duration_months": 12,
        "amount_basis": FinancialEvent.AmountBasis.TODAY,
        "funding_source": FinancialEvent.FundingSource.SEPARATE_SAVINGS,
    },
    "custom": {
        "label": "Custom event",
        "name": "Custom event",
        "category": FinancialEvent.Category.CUSTOM,
        "one_time_amount": "0",
        "recurring_monthly_amount": "0",
        "duration_months": 0,
        "amount_basis": FinancialEvent.AmountBasis.TODAY,
        "funding_source": FinancialEvent.FundingSource.INVESTMENT_PORTFOLIO,
    },
}


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
            "source_mode",
            "source_period_start",
            "source_period_end",
            "source_month_count",
            "source_excluded_income_categories",
            "source_excluded_expense_categories",
            *MONEY_FIELDS,
            *RATE_FIELDS,
            "include_emergency_reserve_in_target",
        )
        widgets = {
            "calculation_date": forms.HiddenInput(),
            "source_mode": forms.HiddenInput(),
            "source_period_start": forms.HiddenInput(),
            "source_period_end": forms.HiddenInput(),
            "source_month_count": forms.HiddenInput(),
            "source_excluded_income_categories": forms.HiddenInput(),
            "source_excluded_expense_categories": forms.HiddenInput(),
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
        for field_name in (
            "source_mode",
            "source_period_start",
            "source_period_end",
            "source_month_count",
            "source_excluded_income_categories",
            "source_excluded_expense_categories",
        ):
            self.fields[field_name].required = False
        self.fields["calculation_date"].initial = self.initial.get("calculation_date", timezone.localdate())

    def clean_birth_date(self) -> date | None:
        birth_date = self.cleaned_data.get("birth_date")
        if birth_date and birth_date > timezone.localdate():
            raise forms.ValidationError("Birth date cannot be in the future.", code="future_birth_date")
        return birth_date

    def clean(self) -> dict[str, Any]:
        cleaned_data = super().clean()
        cleaned_data["source_mode"] = cleaned_data.get("source_mode") or FreedomScenario.SourceMode.MANUAL
        cleaned_data["source_month_count"] = cleaned_data.get("source_month_count") or 0
        cleaned_data["source_excluded_income_categories"] = cleaned_data.get("source_excluded_income_categories") or []
        cleaned_data["source_excluded_expense_categories"] = (
            cleaned_data.get("source_excluded_expense_categories") or []
        )
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
            "source_mode": FreedomScenario.SourceMode.MANUAL,
            "source_period_start": None,
            "source_period_end": None,
            "source_month_count": 0,
            "source_excluded_income_categories": [],
            "source_excluded_expense_categories": [],
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


class FinancialEventForm(forms.ModelForm):
    """Validate one editable draft life event."""

    class Meta:
        model = FinancialEvent
        fields = (
            "name",
            "category",
            "event_date",
            "one_time_amount",
            "amount_basis",
            "recurring_monthly_amount",
            "recurring_end_date",
            "funding_source",
            "sort_order",
            "notes",
        )
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "category": forms.Select(attrs={"class": "form-select"}),
            "event_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "one_time_amount": forms.NumberInput(attrs={"class": "form-control", "inputmode": "decimal"}),
            "amount_basis": forms.Select(attrs={"class": "form-select"}),
            "recurring_monthly_amount": forms.NumberInput(attrs={"class": "form-control", "inputmode": "decimal"}),
            "recurring_end_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "funding_source": forms.Select(attrs={"class": "form-select"}),
            "sort_order": forms.HiddenInput(),
            "notes": forms.TextInput(attrs={"class": "form-control"}),
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        for field_name in ("one_time_amount", "recurring_monthly_amount", "sort_order"):
            self.fields[field_name].required = False
        self.fields["one_time_amount"].initial = self.initial.get("one_time_amount", Decimal("0"))
        self.fields["recurring_monthly_amount"].initial = self.initial.get("recurring_monthly_amount", Decimal("0"))
        self.fields["sort_order"].initial = self.initial.get("sort_order", 0)

    def clean(self) -> dict[str, Any]:
        cleaned_data = super().clean()
        if self.errors:
            return cleaned_data
        one_time = cleaned_data.get("one_time_amount") or Decimal("0")
        recurring = cleaned_data.get("recurring_monthly_amount") or Decimal("0")
        cleaned_data["one_time_amount"] = one_time
        cleaned_data["recurring_monthly_amount"] = recurring
        cleaned_data["sort_order"] = cleaned_data.get("sort_order") or 0
        if one_time <= 0 and recurring <= 0:
            raise forms.ValidationError("Enter a positive one-time or recurring cost.", code="empty_event")
        if recurring > 0 and not cleaned_data.get("recurring_end_date"):
            self.add_error(
                "recurring_end_date",
                forms.ValidationError("A recurring cost requires an end date.", code="missing_recurring_end"),
            )
        return cleaned_data


class BaseFinancialEventFormSet(BaseInlineFormSet):
    """Enforce the event cap and calculation-date relationship across rows."""

    def __init__(self, *args: Any, calculation_date: date | None = None, **kwargs: Any) -> None:
        self.calculation_date = calculation_date
        super().__init__(*args, **kwargs)

    def clean(self) -> None:
        super().clean()
        if any(self.errors):
            return
        active_forms = [form for form in self.forms if form.cleaned_data and not form.cleaned_data.get("DELETE", False)]
        if len(active_forms) > MAX_EVENT_FORMS:
            raise forms.ValidationError(f"At most {MAX_EVENT_FORMS} life events are supported.", code="too_many_events")
        if self.calculation_date:
            for form in active_forms:
                event_date = form.cleaned_data.get("event_date")
                if event_date and event_date <= self.calculation_date:
                    form.add_error(
                        "event_date",
                        forms.ValidationError(
                            "Life events must be after the calculation date.", code="event_not_future"
                        ),
                    )


FinancialEventFormSet = inlineformset_factory(
    FreedomScenario,
    FinancialEvent,
    form=FinancialEventForm,
    formset=BaseFinancialEventFormSet,
    extra=1,
    can_delete=True,
    max_num=MAX_EVENT_FORMS,
    validate_max=True,
)


def event_formset_values(formset: BaseFinancialEventFormSet) -> list[dict[str, Any]]:
    """Return active event rows in explicit stable display order."""

    rows = [form.cleaned_data for form in formset.forms if form.cleaned_data and not form.cleaned_data.get("DELETE")]
    rows.sort(key=lambda row: row.get("sort_order") or 0)
    return [
        {
            **{field: row[field] for field in FinancialEventForm.Meta.fields},
            "sort_order": sort_order,
        }
        for sort_order, row in enumerate(rows)
    ]


class ActualsSnapshotForm(forms.Form):
    """Select a supported owned tracked-finance source period and exclusions."""

    period = forms.ChoiceField(
        choices=(
            ("3m", "Latest 3 complete months"),
            ("6m", "Latest 6 complete months"),
            ("12m", "Latest 12 complete months"),
            ("custom", "Custom year range"),
        ),
        initial="12m",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    start_year = forms.IntegerField(required=False, widget=forms.NumberInput(attrs={"class": "form-control"}))
    end_year = forms.IntegerField(required=False, widget=forms.NumberInput(attrs={"class": "form-control"}))
    excluded_income_categories = forms.MultipleChoiceField(required=False, widget=forms.CheckboxSelectMultiple)
    excluded_expense_categories = forms.MultipleChoiceField(required=False, widget=forms.CheckboxSelectMultiple)

    def __init__(self, *args: Any, user: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        categories = ActualsSnapshotService(user=user).available_categories()
        income_field = cast(forms.MultipleChoiceField, self.fields["excluded_income_categories"])
        expense_field = cast(forms.MultipleChoiceField, self.fields["excluded_expense_categories"])
        income_field.choices = [(value, value) for value in categories["income"]]
        expense_field.choices = [(value, value) for value in categories["expense"]]
        current_year = timezone.localdate().year
        self.fields["start_year"].widget.attrs.update({"min": 2000, "max": current_year})
        self.fields["end_year"].widget.attrs.update({"min": 2000, "max": current_year})

    def clean(self) -> dict[str, Any]:
        cleaned_data = super().clean()
        if self.errors:
            return cleaned_data
        if cleaned_data["period"] == "custom":
            start = cleaned_data.get("start_year")
            end = cleaned_data.get("end_year")
            if start is None or end is None:
                raise forms.ValidationError("Choose both years for a custom range.", code="custom_years_required")
            if start > end:
                raise forms.ValidationError("The start year cannot be after the end year.", code="invalid_year_range")
        return cleaned_data

    def snapshot_options(self) -> dict[str, Any]:
        """Return validated service keyword arguments."""

        return {
            "period": self.cleaned_data["period"],
            "custom_start_year": self.cleaned_data.get("start_year"),
            "custom_end_year": self.cleaned_data.get("end_year"),
            "excluded_income_categories": self.cleaned_data["excluded_income_categories"],
            "excluded_expense_categories": self.cleaned_data["excluded_expense_categories"],
        }
