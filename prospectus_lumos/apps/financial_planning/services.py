from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone

from .calculator import (
    CALCULATION_VERSION,
    AmountBasis,
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


class ImmutableScenarioError(ValidationError):
    """Raised when a caller attempts to mutate a saved scenario."""


class DraftAlreadyExistsError(ValidationError):
    """Raised when a plan already has its one allowed editable draft."""


class FreedomScenarioService:
    """Ownership-safe lifecycle operations for plans and immutable scenarios."""

    def __init__(self, calculator: FinancialFreedomCalculator | None = None) -> None:
        self.calculator = calculator or FinancialFreedomCalculator()

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
