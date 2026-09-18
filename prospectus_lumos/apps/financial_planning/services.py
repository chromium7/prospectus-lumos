from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Max, Q, QuerySet
from django.utils import timezone

from prospectus_lumos.apps.documents.models import Document
from prospectus_lumos.apps.expenses.services import ExpenseAnalyzerService
from prospectus_lumos.apps.transactions.models import Transaction

from .calculator import (
    CALCULATION_VERSION,
    AmountBasis,
    CalculationResult,
    CalculatorInputs,
    EventInput,
    FinancialFreedomCalculator,
    FundingSource,
)
from .models import FinancialEvent, FreedomPlan, FreedomScenario

SCENARIO_INPUT_FIELDS = (
    "calculation_date",
    "birth_date",
    "target_date",
    "source_mode",
    "source_period_start",
    "source_period_end",
    "source_month_count",
    "source_excluded_income_categories",
    "source_excluded_expense_categories",
    "current_monthly_income",
    "current_monthly_expenses",
    "current_monthly_investment",
    "current_investable_assets",
    "emergency_savings",
    "desired_monthly_lifestyle",
    "post_freedom_monthly_income",
    "withdrawal_rate",
    "annual_return_rate",
    "annual_inflation_rate",
    "annual_income_growth_rate",
    "annual_contribution_growth_rate",
    "safety_buffer_rate",
    "include_emergency_reserve_in_target",
)

EVENT_FIELDS = (
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

ACTUALS_PERIOD_MONTHS = {"3m": 3, "6m": 6, "12m": 12}
MONEY_QUANTUM = Decimal("0.01")


@dataclass(frozen=True, slots=True)
class ActualsSnapshot:
    """An owned, dated cash-flow suggestion ready to copy into a draft."""

    period: str
    snapshot_date: date
    source_period_start: date | None
    source_period_end: date | None
    represented_months: tuple[str, ...]
    missing_months: tuple[str, ...]
    total_income: Decimal
    total_expenses: Decimal
    average_income: Decimal
    average_expenses: Decimal
    average_net_savings: Decimal
    income_categories: tuple[str, ...]
    expense_categories: tuple[str, ...]
    excluded_income_categories: tuple[str, ...]
    excluded_expense_categories: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def month_count(self) -> int:
        """Return the number of represented complete months used as denominator."""

        return len(self.represented_months)

    def scenario_values(self) -> dict[str, object]:
        """Return editable suggestions plus frozen source metadata."""

        return {
            "source_mode": FreedomScenario.SourceMode.TRACKED_ACTUALS,
            "source_period_start": self.source_period_start,
            "source_period_end": self.source_period_end,
            "source_month_count": self.month_count,
            "source_excluded_income_categories": list(self.excluded_income_categories),
            "source_excluded_expense_categories": list(self.excluded_expense_categories),
            "current_monthly_income": self.average_income,
            "current_monthly_expenses": self.average_expenses,
            "current_monthly_investment": max(Decimal("0"), self.average_net_savings),
            "desired_monthly_lifestyle": self.average_expenses,
        }

    def to_payload(self) -> dict[str, object]:
        """Return a JSON-safe preview payload with money encoded as strings."""

        return {
            "period": self.period,
            "snapshot_date": self.snapshot_date.isoformat(),
            "source_period_start": self.source_period_start.isoformat() if self.source_period_start else None,
            "source_period_end": self.source_period_end.isoformat() if self.source_period_end else None,
            "month_count": self.month_count,
            "represented_months": list(self.represented_months),
            "missing_months": list(self.missing_months),
            "total_income": _money(self.total_income),
            "total_expenses": _money(self.total_expenses),
            "average_income": _money(self.average_income),
            "average_expenses": _money(self.average_expenses),
            "average_net_savings": _money(self.average_net_savings),
            "income_categories": list(self.income_categories),
            "expense_categories": list(self.expense_categories),
            "excluded_income_categories": list(self.excluded_income_categories),
            "excluded_expense_categories": list(self.excluded_expense_categories),
            "warnings": list(self.warnings),
        }


class ActualsSnapshotService:
    """Build user-scoped cash-flow averages from represented complete months."""

    def __init__(self, *, user: User, snapshot_date: date | None = None) -> None:
        self.user = user
        self.snapshot_date = snapshot_date or timezone.localdate()

    def available_categories(self) -> dict[str, tuple[str, ...]]:
        """Return distinct owned income and expense categories for exclusion controls."""

        rows = Transaction.objects.filter(document__user=self.user).values_list("transaction_type", "category")
        categories: dict[str, set[str]] = {"income": set(), "expense": set()}
        for transaction_type, raw_category in rows:
            categories[transaction_type].add(raw_category or "Uncategorized")
        return {key: tuple(sorted(values)) for key, values in categories.items()}

    def create_snapshot(
        self,
        *,
        period: str,
        custom_start_year: int | None = None,
        custom_end_year: int | None = None,
        excluded_income_categories: Sequence[str] = (),
        excluded_expense_categories: Sequence[str] = (),
    ) -> ActualsSnapshot:
        """Create a snapshot using actual represented months, never transaction count."""

        if period not in (*ACTUALS_PERIOD_MONTHS, "custom"):
            raise ValidationError("Choose a supported actuals period.")
        documents = list(self._complete_documents())
        requested_months: int | None = ACTUALS_PERIOD_MONTHS.get(period)
        source_start: date | None = None
        source_end: date | None = None
        if period == "custom":
            if custom_start_year is None or custom_end_year is None or custom_start_year > custom_end_year:
                raise ValidationError("Choose a valid custom year range.")
            source_start = date(custom_start_year, 1, 1)
            source_end = min(date(custom_end_year, 12, 1), _add_months(self.snapshot_date.replace(day=1), -1))
            documents = [document for document in documents if custom_start_year <= document.year <= custom_end_year]
        elif documents:
            source_end = _document_month(documents[-1])
            source_start = _add_months(source_end, -(requested_months - 1))
            documents = [document for document in documents if source_start <= _document_month(document) <= source_end]

        represented = tuple(_document_month(document) for document in documents)
        represented_labels = tuple(month.strftime("%Y-%m") for month in represented)
        missing = self._missing_months(source_start, source_end, represented) if represented else ()
        categories = self.available_categories()
        excluded_income = tuple(sorted(set(excluded_income_categories) & set(categories["income"])))
        excluded_expenses = tuple(sorted(set(excluded_expense_categories) & set(categories["expense"])))
        analysis = ExpenseAnalyzerService(self.user).get_document_cashflow_analysis(
            document_ids=[document.pk for document in documents],
            exclude_income_categories=list(excluded_income),
            exclude_expense_categories=list(excluded_expenses),
        )
        total_income = Decimal(analysis["total_income"])
        total_expenses = Decimal(analysis["total_expenses"])
        denominator = Decimal(len(documents)) if documents else Decimal("1")
        average_income = (total_income / denominator).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
        average_expenses = (total_expenses / denominator).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
        warnings: list[str] = []
        if not documents:
            warnings.append("No complete tracked months are available; manual planning remains available.")
        if requested_months is not None and len(documents) < requested_months:
            warnings.append(
                f"Requested {requested_months} months; the estimate uses "
                f"{len(documents)} represented complete month(s)."
            )
        if missing:
            warnings.append(
                f"Missing represented calendar months: {', '.join(missing)}. They were not treated as zero."
            )
        if average_expenses > average_income:
            warnings.append("Average tracked expenses exceed average tracked income for this source period.")
        return ActualsSnapshot(
            period=period,
            snapshot_date=self.snapshot_date,
            source_period_start=source_start,
            source_period_end=source_end,
            represented_months=represented_labels,
            missing_months=missing,
            total_income=total_income,
            total_expenses=total_expenses,
            average_income=average_income,
            average_expenses=average_expenses,
            average_net_savings=average_income - average_expenses,
            income_categories=categories["income"],
            expense_categories=categories["expense"],
            excluded_income_categories=excluded_income,
            excluded_expense_categories=excluded_expenses,
            warnings=tuple(warnings),
        )

    def _complete_documents(self) -> QuerySet[Document]:
        current_month = self.snapshot_date.replace(day=1)
        return (
            Document.objects.filter(user=self.user)
            .filter(Q(year__lt=current_month.year) | Q(year=current_month.year, month__lt=current_month.month))
            .order_by("year", "month")
        )

    @staticmethod
    def _missing_months(start: date | None, end: date | None, represented: Sequence[date]) -> tuple[str, ...]:
        if start is None or end is None:
            return ()
        represented_set = set(represented)
        missing: list[str] = []
        cursor = start
        while cursor <= end:
            if cursor not in represented_set:
                missing.append(cursor.strftime("%Y-%m"))
            cursor = _add_months(cursor, 1)
        return tuple(missing)


class ImmutableScenarioError(ValidationError):
    """Raised when a caller attempts to mutate a saved scenario."""


class DraftAlreadyExistsError(ValidationError):
    """Raised when a plan already has its one allowed editable draft."""


class FreedomScenarioService:
    """Ownership-safe lifecycle operations for plans and immutable scenarios."""

    def __init__(self, calculator: FinancialFreedomCalculator | None = None) -> None:
        self.calculator = calculator or FinancialFreedomCalculator()

    def calculate_preview(
        self,
        *,
        scenario_data: Mapping[str, Any],
        events: Sequence[Mapping[str, Any]] = (),
    ) -> CalculationResult:
        """Calculate validated transient inputs without writing a scenario."""

        calculator_inputs = CalculatorInputs(
            calculation_date=scenario_data["calculation_date"],
            target_date=scenario_data["target_date"],
            desired_monthly_lifestyle=scenario_data["desired_monthly_lifestyle"],
            current_investable_assets=scenario_data["current_investable_assets"],
            current_monthly_investment=scenario_data["current_monthly_investment"],
            current_monthly_income=scenario_data["current_monthly_income"],
            current_monthly_expenses=scenario_data["current_monthly_expenses"],
            post_freedom_monthly_income=scenario_data["post_freedom_monthly_income"],
            emergency_savings=scenario_data["emergency_savings"],
            withdrawal_rate=scenario_data["withdrawal_rate"],
            annual_return_rate=scenario_data["annual_return_rate"],
            annual_inflation_rate=scenario_data["annual_inflation_rate"],
            annual_income_growth_rate=scenario_data["annual_income_growth_rate"],
            annual_contribution_growth_rate=scenario_data["annual_contribution_growth_rate"],
            safety_buffer_rate=scenario_data["safety_buffer_rate"],
            include_emergency_reserve_in_target=scenario_data["include_emergency_reserve_in_target"],
        )
        event_inputs = tuple(
            EventInput(
                name=event["name"],
                event_date=event["event_date"],
                one_time_amount=event.get("one_time_amount", Decimal("0")),
                recurring_monthly_amount=event.get("recurring_monthly_amount", Decimal("0")),
                recurring_end_date=event.get("recurring_end_date"),
                amount_basis=AmountBasis(event.get("amount_basis", AmountBasis.TODAY)),
                funding_source=FundingSource(event.get("funding_source", FundingSource.INVESTMENT_PORTFOLIO)),
            )
            for event in events
        )
        return self.calculator.calculate(calculator_inputs, event_inputs)

    @transaction.atomic
    def create_plan_with_draft(
        self,
        *,
        user: User,
        plan_data: Mapping[str, Any],
        scenario_data: Mapping[str, Any],
        events: Sequence[Mapping[str, Any]] = (),
    ) -> FreedomScenario:
        """Create an owned plan and its version-zero draft atomically."""

        plan = FreedomPlan(
            user=user,
            name=str(plan_data["name"]).strip(),
            description=str(plan_data.get("description", "")).strip(),
        )
        plan.full_clean()
        plan.save()
        return self._create_draft(plan=plan, scenario_data=scenario_data, events=events)

    @transaction.atomic
    def update_draft(
        self,
        *,
        user: User,
        draft: FreedomScenario,
        scenario_data: Mapping[str, Any],
        events: Sequence[Mapping[str, Any]] | None = None,
    ) -> FreedomScenario:
        """Replace editable draft inputs and optionally its event set."""

        draft = self._locked_owned_scenario(user=user, scenario=draft)
        self._require_draft(draft)
        for field in SCENARIO_INPUT_FIELDS:
            if field in scenario_data:
                setattr(draft, field, scenario_data[field])
        draft.full_clean(exclude=self._output_field_names())
        draft.save(update_fields=(*SCENARIO_INPUT_FIELDS,))
        if events is not None:
            draft.events.all().delete()
            self._create_events(draft, events)
        return self._calculate_and_store(draft)

    @transaction.atomic
    def save_draft(self, *, user: User, draft: FreedomScenario) -> FreedomScenario:
        """Recalculate and convert a draft into the next immutable saved version."""

        plan = FreedomPlan.objects.select_for_update().get(pk=draft.plan_id)
        if plan.user_id != user.pk:
            raise PermissionDenied("This plan does not belong to the current user.")
        draft = FreedomScenario.objects.select_for_update().prefetch_related("events").get(pk=draft.pk)
        self._require_draft(draft)
        draft = self._calculate_and_store(draft)
        latest_version = (
            FreedomScenario.objects.filter(plan=plan, status=FreedomScenario.Status.SAVED).aggregate(Max("version"))[
                "version__max"
            ]
            or 0
        )
        draft.version = latest_version + 1
        draft.status = FreedomScenario.Status.SAVED
        draft.saved_at = timezone.now()
        try:
            draft.save(update_fields=("version", "status", "saved_at"))
        except IntegrityError as exc:
            raise ValidationError("A scenario version was allocated concurrently; please retry.") from exc
        return draft

    @transaction.atomic
    def clone_to_draft(self, *, user: User, scenario: FreedomScenario) -> FreedomScenario:
        """Copy a saved scenario and its events to a new editable draft."""

        scenario = self._locked_owned_scenario(user=user, scenario=scenario)
        if scenario.status != FreedomScenario.Status.SAVED:
            raise ValidationError("Only saved scenarios can start an updated version.")
        return self._create_draft(
            plan=scenario.plan,
            scenario_data=self._scenario_values(scenario),
            events=[self._event_values(event) for event in scenario.events.all()],
            based_on=scenario,
        )

    @transaction.atomic
    def duplicate_to_new_plan(
        self, *, user: User, scenario: FreedomScenario, name: str | None = None
    ) -> FreedomScenario:
        """Copy a saved scenario into an editable draft under a new owned plan."""

        scenario = self._locked_owned_scenario(user=user, scenario=scenario)
        if scenario.status != FreedomScenario.Status.SAVED:
            raise ValidationError("Only saved scenarios can be duplicated.")
        plan = FreedomPlan.objects.create(user=user, name=(name or f"Copy of {scenario.plan.name}")[:120])
        return self._create_draft(
            plan=plan,
            scenario_data=self._scenario_values(scenario),
            events=[self._event_values(event) for event in scenario.events.all()],
            based_on=scenario,
        )

    def rename_plan(self, *, user: User, plan: FreedomPlan, name: str) -> FreedomPlan:
        """Rename one owned plan."""

        self._require_owned_plan(user=user, plan=plan)
        plan.name = name.strip()
        plan.full_clean()
        plan.save(update_fields=("name", "updated_at"))
        return plan

    def set_archived(self, *, user: User, plan: FreedomPlan, archived: bool) -> FreedomPlan:
        """Archive or restore one owned plan."""

        self._require_owned_plan(user=user, plan=plan)
        plan.is_archived = archived
        plan.save(update_fields=("is_archived", "updated_at"))
        return plan

    def _create_draft(
        self,
        *,
        plan: FreedomPlan,
        scenario_data: Mapping[str, Any],
        events: Sequence[Mapping[str, Any]],
        based_on: FreedomScenario | None = None,
    ) -> FreedomScenario:
        if FreedomScenario.objects.filter(plan=plan, status=FreedomScenario.Status.DRAFT).exists():
            raise DraftAlreadyExistsError("This plan already has an editable draft.")
        values = {field: scenario_data[field] for field in SCENARIO_INPUT_FIELDS if field in scenario_data}
        draft = FreedomScenario(plan=plan, version=0, status=FreedomScenario.Status.DRAFT, based_on=based_on, **values)
        draft.full_clean(exclude=self._output_field_names())
        draft.save()
        self._create_events(draft, events)
        return self._calculate_and_store(draft)

    def _calculate_and_store(self, draft: FreedomScenario) -> FreedomScenario:
        result = self.calculator.calculate(self._calculator_inputs(draft), self._event_inputs(draft))
        base_case = result.cases["base"]
        draft.base_freedom_number = result.target.base_freedom_number_today
        draft.total_target = result.target.total_target
        draft.required_monthly_investment = base_case.solver.monthly_contribution
        draft.projected_achievement_date = base_case.achievement.achievement_date
        draft.funding_gap = result.funding_gap
        draft.progress_percent = min(Decimal("99999.999"), result.progress_percent)
        draft.result_status = result.status.value
        draft.projection_data = result.to_payload()
        draft.calculation_version = CALCULATION_VERSION
        draft.save(
            update_fields=(
                "base_freedom_number",
                "total_target",
                "required_monthly_investment",
                "projected_achievement_date",
                "funding_gap",
                "progress_percent",
                "result_status",
                "projection_data",
                "calculation_version",
            )
        )
        return draft

    @staticmethod
    def _calculator_inputs(scenario: FreedomScenario) -> CalculatorInputs:
        return CalculatorInputs(
            calculation_date=scenario.calculation_date,
            target_date=scenario.target_date,
            desired_monthly_lifestyle=scenario.desired_monthly_lifestyle,
            current_investable_assets=scenario.current_investable_assets,
            current_monthly_investment=scenario.current_monthly_investment,
            current_monthly_income=scenario.current_monthly_income,
            current_monthly_expenses=scenario.current_monthly_expenses,
            post_freedom_monthly_income=scenario.post_freedom_monthly_income,
            emergency_savings=scenario.emergency_savings,
            withdrawal_rate=scenario.withdrawal_rate,
            annual_return_rate=scenario.annual_return_rate,
            annual_inflation_rate=scenario.annual_inflation_rate,
            annual_income_growth_rate=scenario.annual_income_growth_rate,
            annual_contribution_growth_rate=scenario.annual_contribution_growth_rate,
            safety_buffer_rate=scenario.safety_buffer_rate,
            include_emergency_reserve_in_target=scenario.include_emergency_reserve_in_target,
        )

    @staticmethod
    def _event_inputs(scenario: FreedomScenario) -> tuple[EventInput, ...]:
        return tuple(
            EventInput(
                name=event.name,
                event_date=event.event_date,
                one_time_amount=event.one_time_amount,
                recurring_monthly_amount=event.recurring_monthly_amount,
                recurring_end_date=event.recurring_end_date,
                amount_basis=AmountBasis(event.amount_basis),
                funding_source=FundingSource(event.funding_source),
            )
            for event in scenario.events.all()
        )

    @staticmethod
    def _create_events(scenario: FreedomScenario, events: Sequence[Mapping[str, Any]]) -> None:
        for event_data in events:
            values = {field: event_data[field] for field in EVENT_FIELDS if field in event_data}
            event = FinancialEvent(scenario=scenario, **values)
            event.full_clean()
            event.save()

    @staticmethod
    def _scenario_values(scenario: FreedomScenario) -> dict[str, Any]:
        values = {field: getattr(scenario, field) for field in SCENARIO_INPUT_FIELDS}
        return values

    @staticmethod
    def _event_values(event: FinancialEvent) -> dict[str, Any]:
        return {field: getattr(event, field) for field in EVENT_FIELDS}

    @staticmethod
    def _locked_owned_scenario(*, user: User, scenario: FreedomScenario) -> FreedomScenario:
        owned = FreedomScenario.objects.select_for_update().select_related("plan").get(pk=scenario.pk)
        if owned.plan.user_id != user.pk:
            raise PermissionDenied("This scenario does not belong to the current user.")
        return owned

    @staticmethod
    def _require_owned_plan(*, user: User, plan: FreedomPlan) -> None:
        if plan.user_id != user.pk:
            raise PermissionDenied("This plan does not belong to the current user.")

    @staticmethod
    def _require_draft(scenario: FreedomScenario) -> None:
        if scenario.status != FreedomScenario.Status.DRAFT:
            raise ImmutableScenarioError("Saved scenarios are immutable; create an updated version instead.")

    @staticmethod
    def _output_field_names() -> tuple[str, ...]:
        return (
            "base_freedom_number",
            "total_target",
            "required_monthly_investment",
            "projected_achievement_date",
            "funding_gap",
            "progress_percent",
            "result_status",
            "projection_data",
        )


def _document_month(document: Document) -> date:
    return date(document.year, document.month, 1)


def _add_months(start: date, months: int) -> date:
    month_number = start.year * 12 + start.month - 1 + months
    return date(month_number // 12, month_number % 12 + 1, 1)


def _money(value: Decimal) -> str:
    return format(value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP), "f")
