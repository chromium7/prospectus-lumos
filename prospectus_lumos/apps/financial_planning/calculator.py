"""Deterministic financial-freedom calculations.

Dates are normalized to the first of their calendar month.  The calculation
month is the opening snapshot (month zero), so the first projection row is the
following month. Contributions arrive at month end, after investment growth
and before that month's event outflows. Recurring event end months are
inclusive. Events in or before the target month are accumulation deductions;
later portfolio-funded events are reserved once in the target.

Percentage inputs use percentage points (``Decimal("7")`` means 7%). Money is
kept as :class:`~decimal.Decimal` without intermediate rounding. JSON money is
rounded half-up to two decimal places and encoded as strings. Rates are also
encoded as strings, so authoritative output never contains binary floats.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import date
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal, localcontext
from enum import StrEnum
from typing import Mapping, Sequence

SCHEMA_VERSION = 1
CALCULATION_VERSION = 1
CURRENCY = "IDR"

DEFAULT_WITHDRAWAL_RATE = Decimal("4")
DEFAULT_ANNUAL_RETURN_RATE = Decimal("7")
DEFAULT_ANNUAL_INFLATION_RATE = Decimal("3")
DEFAULT_SAFETY_BUFFER_RATE = Decimal("10")
DEFAULT_ANNUAL_INCOME_GROWTH_RATE = Decimal("0")
DEFAULT_ANNUAL_CONTRIBUTION_GROWTH_RATE = Decimal("0")

CONSERVATIVE_RETURN_ADJUSTMENT = Decimal("-2")
CONSERVATIVE_WITHDRAWAL_ADJUSTMENT = Decimal("-0.5")
OPTIMISTIC_RETURN_ADJUSTMENT = Decimal("2")
MIN_CONSERVATIVE_RETURN_RATE = Decimal("-99")
MIN_CONSERVATIVE_WITHDRAWAL_RATE = Decimal("0.5")

MIN_ANNUAL_RATE = Decimal("-99")
MAX_ANNUAL_RATE = Decimal("100")
MAX_WITHDRAWAL_RATE = Decimal("10")
MAX_SAFETY_BUFFER_RATE = Decimal("100")
MAX_EVENTS = 50

SOLVER_TOLERANCE = Decimal("1000")
SOLVER_INITIAL_UPPER_BOUND = Decimal("10000000")
SOLVER_MAX_UPPER_BOUND = Decimal("1000000000000")
SOLVER_MAX_ITERATIONS = 64
MAX_PROJECTION_MONTHS = 100 * 12

ZERO = Decimal("0")
ONE_HUNDRED = Decimal("100")
MONEY_QUANTUM = Decimal("0.01")
WHOLE_RUPIAH = Decimal("1")


class FundingSource(StrEnum):
    """Supported sources for a life event."""

    INVESTMENT_PORTFOLIO = "investment_portfolio"
    SEPARATE_SAVINGS = "separate_savings"


class AmountBasis(StrEnum):
    """Whether event amounts are current or already nominal values."""

    TODAY = "today"
    EVENT_DATE = "event_date"


class SolverStatus(StrEnum):
    """Required-contribution solver states."""

    SOLVED = "solved"
    UNREACHABLE = "unreachable_within_solver_bounds"


class AchievementStatus(StrEnum):
    """Current-pace search states."""

    REACHED = "reached"
    NOT_REACHED = "not_reached_within_horizon"


class ResultStatus(StrEnum):
    """Headline scenario states."""

    ON_TRACK = "on_track"
    BEHIND = "behind"
    UNREACHABLE = "unreachable"
    COMPLETE = "complete"


class WarningCode(StrEnum):
    """Stable non-fatal calculation warning codes."""

    LIFESTYLE_FULLY_FUNDED = "lifestyle_fully_funded_by_income"
    CONTRIBUTION_EXCEEDS_SURPLUS = "contribution_exceeds_monthly_surplus"
    EVENT_SHORTFALL = "portfolio_event_shortfall"
    SEPARATE_SAVINGS = "separate_savings_required"
    SOLVER_UNREACHABLE = "unreachable_within_solver_bounds"
    PACE_NOT_REACHED = "not_reached_within_horizon"


class InputErrorCode(StrEnum):
    """Stable normalized-input validation error codes."""

    INVALID_DATE_RANGE = "invalid_date_range"
    NEGATIVE_MONEY = "negative_money"
    INVALID_WITHDRAWAL_RATE = "invalid_withdrawal_rate"
    INVALID_RATE = "invalid_rate"
    INVALID_SAFETY_BUFFER = "invalid_safety_buffer"
    INVALID_EVENT = "invalid_event"
    TOO_MANY_EVENTS = "too_many_events"


class CalculatorInputError(ValueError):
    """Raised when normalized domain input violates the calculation contract."""

    def __init__(self, code: InputErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class CalculatorInputs:
    """Plain, database-independent inputs to a financial-freedom calculation."""

    calculation_date: date
    target_date: date
    desired_monthly_lifestyle: Decimal
    current_investable_assets: Decimal = ZERO
    current_monthly_investment: Decimal = ZERO
    current_monthly_income: Decimal = ZERO
    current_monthly_expenses: Decimal = ZERO
    post_freedom_monthly_income: Decimal = ZERO
    emergency_savings: Decimal = ZERO
    withdrawal_rate: Decimal = DEFAULT_WITHDRAWAL_RATE
    annual_return_rate: Decimal = DEFAULT_ANNUAL_RETURN_RATE
    annual_inflation_rate: Decimal = DEFAULT_ANNUAL_INFLATION_RATE
    annual_income_growth_rate: Decimal = DEFAULT_ANNUAL_INCOME_GROWTH_RATE
    annual_contribution_growth_rate: Decimal = DEFAULT_ANNUAL_CONTRIBUTION_GROWTH_RATE
    safety_buffer_rate: Decimal = DEFAULT_SAFETY_BUFFER_RATE
    include_emergency_reserve_in_target: bool = False

    def normalized(self) -> CalculatorInputs:
        """Return an equivalent input with dates normalized to month starts."""

        return replace(
            self,
            calculation_date=_month_start(self.calculation_date),
            target_date=_month_start(self.target_date),
        )


@dataclass(frozen=True, slots=True)
class EventInput:
    """A one-time and/or inclusive temporary recurring life event."""

    name: str
    event_date: date
    one_time_amount: Decimal = ZERO
    recurring_monthly_amount: Decimal = ZERO
    recurring_end_date: date | None = None
    amount_basis: AmountBasis = AmountBasis.TODAY
    funding_source: FundingSource = FundingSource.INVESTMENT_PORTFOLIO

    def normalized(self) -> EventInput:
        """Return an equivalent event with dates normalized to month starts."""

        return replace(
            self,
            name=self.name.strip(),
            event_date=_month_start(self.event_date),
            recurring_end_date=_month_start(self.recurring_end_date) if self.recurring_end_date else None,
        )


@dataclass(frozen=True, slots=True)
class TargetBreakdown:
    """Components of the total target for one month."""

    base_freedom_number_today: Decimal
    base_freedom_number_at_target: Decimal
    post_target_event_reserve: Decimal
    emergency_reserve: Decimal
    target_before_buffer: Decimal
    safety_buffer: Decimal
    total_target: Decimal


@dataclass(frozen=True, slots=True)
class MonthlyProjection:
    """One end-of-month portfolio ledger row."""

    date: date
    opening_balance: Decimal
    contribution: Decimal
    growth: Decimal
    event_outflow: Decimal
    event_shortfall: Decimal
    closing_balance: Decimal
    target: Decimal


@dataclass(frozen=True, slots=True)
class AnnualProjection:
    """A deterministic aggregation of consecutive monthly rows."""

    year: int
    opening_balance: Decimal
    contributions: Decimal
    growth: Decimal
    event_outflows: Decimal
    event_shortfalls: Decimal
    closing_balance: Decimal
    target: Decimal


@dataclass(frozen=True, slots=True)
class Projection:
    """Monthly portfolio ledger and its ending balance."""

    monthly: tuple[MonthlyProjection, ...]
    ending_balance: Decimal
    total_event_shortfall: Decimal


@dataclass(frozen=True, slots=True)
class SolverResult:
    """Bounded required-contribution search result."""

    status: SolverStatus
    monthly_contribution: Decimal | None
    iterations: int


@dataclass(frozen=True, slots=True)
class AchievementResult:
    """First sustainable current-pace target crossing."""

    status: AchievementStatus
    achievement_date: date | None
    month_index: int | None


@dataclass(frozen=True, slots=True)
class CaseResult:
    """Headline output for one deterministic assumption case."""

    annual_return_rate: Decimal
    withdrawal_rate: Decimal
    target: TargetBreakdown
    solver: SolverResult
    achievement: AchievementResult


@dataclass(frozen=True, slots=True)
class SeparateSavingsRequirement:
    """Disclosure for an event excluded from portfolio calculations."""

    name: str
    event_date: date
    nominal_total: Decimal
    monthly_funding_need: Decimal


@dataclass(frozen=True, slots=True)
class CalculationResult:
    """Complete versioned result returned by the public calculator interface."""

    inputs: CalculatorInputs
    target: TargetBreakdown
    projection: Projection
    annual: tuple[AnnualProjection, ...]
    cases: Mapping[str, CaseResult]
    separate_savings: tuple[SeparateSavingsRequirement, ...]
    status: ResultStatus
    funding_gap: Decimal
    progress_percent: Decimal
    current_monthly_surplus: Decimal
    target_monthly_income: Decimal
    target_monthly_expenses: Decimal
    target_monthly_surplus: Decimal
    warnings: tuple[WarningCode, ...]

    def to_payload(self) -> dict[str, object]:
        """Return a stable JSON-compatible payload with no Decimal objects or floats."""

        return {
            "schema_version": SCHEMA_VERSION,
            "calculation_version": CALCULATION_VERSION,
            "currency": CURRENCY,
            "status": self.status,
            "funding_gap": _money(self.funding_gap),
            "progress_percent": _decimal_string(self.progress_percent, Decimal("0.001")),
            "affordability": {
                "current_monthly_surplus": _money(self.current_monthly_surplus),
                "target_monthly_income": _money(self.target_monthly_income),
                "target_monthly_expenses": _money(self.target_monthly_expenses),
                "target_monthly_surplus": _money(self.target_monthly_surplus),
            },
            "target_breakdown": _target_payload(self.target),
            "monthly": [_monthly_payload(row) for row in self.projection.monthly],
            "annual": [_annual_payload(row) for row in self.annual],
            "cases": {name: _case_payload(case) for name, case in self.cases.items()},
            "separate_savings": [
                {
                    "name": item.name,
                    "event_date": item.event_date.isoformat(),
                    "nominal_total": _money(item.nominal_total),
                    "monthly_funding_need": _money(item.monthly_funding_need),
                }
                for item in self.separate_savings
            ],
            "warnings": list(self.warnings),
        }

    def to_json(self) -> str:
        """Serialize the result to deterministic, byte-stable JSON."""

        return json.dumps(self.to_payload(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class _EventSchedule:
    portfolio: Mapping[int, Decimal]
    separate: Mapping[int, Decimal]
    separate_requirements: tuple[SeparateSavingsRequirement, ...]


class FinancialFreedomCalculator:
    """Pure calculator for target, projection, solver, and scenario cases."""

    def calculate(self, inputs: CalculatorInputs, events: Sequence[EventInput] = ()) -> CalculationResult:
        """Validate and calculate one deterministic, database-independent scenario."""

        normalized_inputs = inputs.normalized()
        normalized_events = tuple(event.normalized() for event in events)
        self._validate(normalized_inputs, normalized_events)
        schedule = self._event_schedule(normalized_inputs, normalized_events)

        base_case = self._calculate_case(normalized_inputs, schedule)
        conservative_inputs = replace(
            normalized_inputs,
            annual_return_rate=max(
                MIN_CONSERVATIVE_RETURN_RATE,
                normalized_inputs.annual_return_rate + CONSERVATIVE_RETURN_ADJUSTMENT,
            ),
            withdrawal_rate=min(
                normalized_inputs.withdrawal_rate,
                max(
                    MIN_CONSERVATIVE_WITHDRAWAL_RATE,
                    normalized_inputs.withdrawal_rate + CONSERVATIVE_WITHDRAWAL_ADJUSTMENT,
                ),
            ),
        )
        optimistic_inputs = replace(
            normalized_inputs,
            annual_return_rate=min(
                MAX_ANNUAL_RATE, normalized_inputs.annual_return_rate + OPTIMISTIC_RETURN_ADJUSTMENT
            ),
        )
        cases = {
            "conservative": self._calculate_case(conservative_inputs, schedule),
            "base": base_case,
            "optimistic": self._calculate_case(optimistic_inputs, schedule),
        }

        final_month = max(
            _months_between(normalized_inputs.calculation_date, normalized_inputs.target_date),
            base_case.achievement.month_index or 0,
        )
        projection = self.project(
            normalized_inputs,
            schedule,
            normalized_inputs.current_monthly_investment,
            min(final_month, MAX_PROJECTION_MONTHS),
        )
        warnings = self._warnings(normalized_inputs, schedule, projection, base_case)
        required = base_case.solver.monthly_contribution
        funding_gap = max(ZERO, (required or ZERO) - normalized_inputs.current_monthly_investment)
        current_target = self.target_for_month(normalized_inputs, schedule, 0)
        progress = (
            Decimal("100")
            if current_target.total_target == ZERO
            else normalized_inputs.current_investable_assets / current_target.total_target * ONE_HUNDRED
        )
        status = self._result_status(normalized_inputs, current_target, base_case)
        target_month = _months_between(normalized_inputs.calculation_date, normalized_inputs.target_date)
        target_years = target_month // 12
        current_surplus = max(
            ZERO,
            normalized_inputs.current_monthly_income - normalized_inputs.current_monthly_expenses,
        )
        target_income = normalized_inputs.current_monthly_income * _annual_growth_factor(
            normalized_inputs.annual_income_growth_rate, target_years
        )
        target_expenses = normalized_inputs.current_monthly_expenses * _growth_factor(
            normalized_inputs.annual_inflation_rate, target_month
        )

        return CalculationResult(
            inputs=normalized_inputs,
            target=base_case.target,
            projection=projection,
            annual=self._annualize(projection.monthly),
            cases=cases,
            separate_savings=schedule.separate_requirements,
            status=status,
            funding_gap=funding_gap,
            progress_percent=progress,
            current_monthly_surplus=current_surplus,
            target_monthly_income=target_income,
            target_monthly_expenses=target_expenses,
            target_monthly_surplus=max(ZERO, target_income - target_expenses),
            warnings=warnings,
        )

    def target_for_month(
        self,
        inputs: CalculatorInputs,
        schedule_or_events: _EventSchedule | Sequence[EventInput],
        month_index: int,
    ) -> TargetBreakdown:
        """Calculate the nominal target and its components for a month index."""

        schedule = (
            schedule_or_events
            if isinstance(schedule_or_events, _EventSchedule)
            else self._event_schedule(inputs, tuple(event.normalized() for event in schedule_or_events))
        )
        unfunded_monthly = max(ZERO, inputs.desired_monthly_lifestyle - inputs.post_freedom_monthly_income)
        base_today = unfunded_monthly * Decimal("12") / _percentage(inputs.withdrawal_rate)
        base_at_month = base_today * _growth_factor(inputs.annual_inflation_rate, month_index)
        monthly_return_factor = _growth_factor(inputs.annual_return_rate, 1)
        post_target_reserve = ZERO
        for event_month, amount in schedule.portfolio.items():
            if event_month > month_index:
                post_target_reserve += amount / (monthly_return_factor ** (event_month - month_index))
        emergency = inputs.emergency_savings if inputs.include_emergency_reserve_in_target else ZERO
        before_buffer = base_at_month + post_target_reserve + emergency
        buffer = before_buffer * _percentage(inputs.safety_buffer_rate)
        return TargetBreakdown(
            base_freedom_number_today=base_today,
            base_freedom_number_at_target=base_at_month,
            post_target_event_reserve=post_target_reserve,
            emergency_reserve=emergency,
            target_before_buffer=before_buffer,
            safety_buffer=buffer,
            total_target=before_buffer + buffer,
        )

    def project(
        self,
        inputs: CalculatorInputs,
        schedule_or_events: _EventSchedule | Sequence[EventInput],
        starting_monthly_contribution: Decimal,
        months: int,
    ) -> Projection:
        """Project an end-of-month portfolio ledger for an exact number of months."""

        if months < 0 or months > MAX_PROJECTION_MONTHS:
            raise CalculatorInputError(InputErrorCode.INVALID_DATE_RANGE, "Projection months are outside the horizon.")
        schedule = (
            schedule_or_events
            if isinstance(schedule_or_events, _EventSchedule)
            else self._event_schedule(inputs, tuple(event.normalized() for event in schedule_or_events))
        )
        monthly_return_factor = _growth_factor(inputs.annual_return_rate, 1)
        monthly_return = monthly_return_factor - Decimal("1")
        monthly_inflation_factor = _growth_factor(inputs.annual_inflation_rate, 1)
        current_target = self.target_for_month(inputs, schedule, 0)
        base_at_month = current_target.base_freedom_number_at_target
        event_reserve = current_target.post_target_event_reserve
        buffer_multiplier = Decimal("1") + _percentage(inputs.safety_buffer_rate)
        rows: list[MonthlyProjection] = []
        balance = inputs.current_investable_assets
        total_shortfall = ZERO
        for month_index in range(1, months + 1):
            base_at_month *= monthly_inflation_factor
            event_reserve = max(
                ZERO,
                event_reserve * monthly_return_factor - schedule.portfolio.get(month_index, ZERO),
            )
            target = (base_at_month + event_reserve + current_target.emergency_reserve) * buffer_multiplier
            opening = balance
            growth = opening * monthly_return
            contribution_year = (month_index - 1) // 12
            contribution = starting_monthly_contribution * _annual_growth_factor(
                inputs.annual_contribution_growth_rate, contribution_year
            )
            requested_outflow = schedule.portfolio.get(month_index, ZERO)
            available = opening + growth + contribution
            shortfall = max(ZERO, requested_outflow - max(ZERO, available))
            balance = max(ZERO, available - requested_outflow)
            total_shortfall += shortfall
            rows.append(
                MonthlyProjection(
                    date=_add_months(inputs.calculation_date, month_index),
                    opening_balance=opening,
                    contribution=contribution,
                    growth=growth,
                    event_outflow=requested_outflow,
                    event_shortfall=shortfall,
                    closing_balance=balance,
                    target=target,
                )
            )
        return Projection(tuple(rows), balance, total_shortfall)

    def solve_required_contribution(
        self,
        inputs: CalculatorInputs,
        schedule_or_events: _EventSchedule | Sequence[EventInput],
    ) -> SolverResult:
        """Find a reaching starting contribution within the documented Rp1,000 tolerance."""

        schedule = (
            schedule_or_events
            if isinstance(schedule_or_events, _EventSchedule)
            else self._event_schedule(inputs, tuple(event.normalized() for event in schedule_or_events))
        )
        target_month = _months_between(inputs.calculation_date, inputs.target_date)
        target = self.target_for_month(inputs, schedule, target_month).total_target
        if inputs.current_investable_assets >= self.target_for_month(inputs, schedule, 0).total_target:
            return SolverResult(SolverStatus.SOLVED, ZERO, 0)

        def reaches(contribution: Decimal) -> bool:
            ending_balance, shortfall = self._ending_state(inputs, schedule, contribution, target_month)
            return ending_balance >= target and shortfall == ZERO

        if reaches(ZERO):
            return SolverResult(SolverStatus.SOLVED, ZERO, 0)
        upper = SOLVER_INITIAL_UPPER_BOUND
        while upper < SOLVER_MAX_UPPER_BOUND and not reaches(upper):
            upper = min(SOLVER_MAX_UPPER_BOUND, upper * Decimal("2"))
        if not reaches(upper):
            return SolverResult(SolverStatus.UNREACHABLE, None, 0)

        lower = ZERO
        iterations = 0
        while upper - lower > SOLVER_TOLERANCE and iterations < SOLVER_MAX_ITERATIONS:
            midpoint = (lower + upper) / Decimal("2")
            if reaches(midpoint):
                upper = midpoint
            else:
                lower = midpoint
            iterations += 1
        result = upper.quantize(WHOLE_RUPIAH, rounding=ROUND_CEILING)
        return SolverResult(SolverStatus.SOLVED, result, iterations)

    def find_achievement_date(
        self,
        inputs: CalculatorInputs,
        schedule_or_events: _EventSchedule | Sequence[EventInput],
    ) -> AchievementResult:
        """Return the first sustainable crossing at the user's current contribution pace."""

        schedule = (
            schedule_or_events
            if isinstance(schedule_or_events, _EventSchedule)
            else self._event_schedule(inputs, tuple(event.normalized() for event in schedule_or_events))
        )
        if inputs.current_investable_assets >= self.target_for_month(inputs, schedule, 0).total_target:
            return AchievementResult(AchievementStatus.REACHED, inputs.calculation_date, 0)
        monthly_return_factor = _growth_factor(inputs.annual_return_rate, 1)
        monthly_inflation_factor = _growth_factor(inputs.annual_inflation_rate, 1)
        current_target = self.target_for_month(inputs, schedule, 0)
        base_at_month = current_target.base_freedom_number_at_target
        event_reserve = current_target.post_target_event_reserve
        buffer_multiplier = Decimal("1") + _percentage(inputs.safety_buffer_rate)
        balance = inputs.current_investable_assets
        contribution = inputs.current_monthly_investment
        contribution_growth = Decimal("1") + _percentage(inputs.annual_contribution_growth_rate)
        cumulative_shortfall = ZERO
        for month_index in range(1, MAX_PROJECTION_MONTHS + 1):
            if month_index > 1 and (month_index - 1) % 12 == 0:
                contribution *= contribution_growth
            requested_outflow = schedule.portfolio.get(month_index, ZERO)
            available = balance * monthly_return_factor + contribution
            shortfall = max(ZERO, requested_outflow - max(ZERO, available))
            cumulative_shortfall += shortfall
            balance = max(ZERO, available - requested_outflow)
            base_at_month *= monthly_inflation_factor
            event_reserve = max(ZERO, event_reserve * monthly_return_factor - requested_outflow)
            target = (base_at_month + event_reserve + current_target.emergency_reserve) * buffer_multiplier
            if cumulative_shortfall == ZERO and balance >= target:
                achievement_date = _add_months(inputs.calculation_date, month_index)
                return AchievementResult(AchievementStatus.REACHED, achievement_date, month_index)
        return AchievementResult(AchievementStatus.NOT_REACHED, None, None)

    @staticmethod
    def _ending_state(
        inputs: CalculatorInputs,
        schedule: _EventSchedule,
        starting_monthly_contribution: Decimal,
        months: int,
    ) -> tuple[Decimal, Decimal]:
        """Return only ending balance and shortfall for efficient solver probes."""

        monthly_return_factor = _growth_factor(inputs.annual_return_rate, 1)
        contribution_growth = Decimal("1") + _percentage(inputs.annual_contribution_growth_rate)
        contribution = starting_monthly_contribution
        balance = inputs.current_investable_assets
        total_shortfall = ZERO
        for month_index in range(1, months + 1):
            if month_index > 1 and (month_index - 1) % 12 == 0:
                contribution *= contribution_growth
            available = balance * monthly_return_factor + contribution
            requested_outflow = schedule.portfolio.get(month_index, ZERO)
            total_shortfall += max(ZERO, requested_outflow - max(ZERO, available))
            balance = max(ZERO, available - requested_outflow)
        return balance, total_shortfall

    def _calculate_case(self, inputs: CalculatorInputs, schedule: _EventSchedule) -> CaseResult:
        target_month = _months_between(inputs.calculation_date, inputs.target_date)
        return CaseResult(
            annual_return_rate=inputs.annual_return_rate,
            withdrawal_rate=inputs.withdrawal_rate,
            target=self.target_for_month(inputs, schedule, target_month),
            solver=self.solve_required_contribution(inputs, schedule),
            achievement=self.find_achievement_date(inputs, schedule),
        )

    def _event_schedule(self, inputs: CalculatorInputs, events: Sequence[EventInput]) -> _EventSchedule:
        portfolio: dict[int, Decimal] = {}
        separate: dict[int, Decimal] = {}
        separate_requirements: list[SeparateSavingsRequirement] = []
        for event in events:
            start_month = _months_between(inputs.calculation_date, event.event_date)
            end_month = (
                _months_between(inputs.calculation_date, event.recurring_end_date)
                if event.recurring_end_date
                else start_month
            )
            destination = portfolio if event.funding_source == FundingSource.INVESTMENT_PORTFOLIO else separate
            event_total = ZERO
            if event.one_time_amount:
                amount = self._nominal_event_amount(inputs, event, event.one_time_amount, start_month)
                destination[start_month] = destination.get(start_month, ZERO) + amount
                event_total += amount
            if event.recurring_monthly_amount:
                for month_index in range(start_month, end_month + 1):
                    amount = self._nominal_event_amount(inputs, event, event.recurring_monthly_amount, month_index)
                    destination[month_index] = destination.get(month_index, ZERO) + amount
                    event_total += amount
            if event.funding_source == FundingSource.SEPARATE_SAVINGS:
                months_to_event = max(1, start_month)
                separate_requirements.append(
                    SeparateSavingsRequirement(
                        name=event.name,
                        event_date=event.event_date,
                        nominal_total=event_total,
                        monthly_funding_need=event_total / Decimal(months_to_event),
                    )
                )
        return _EventSchedule(portfolio, separate, tuple(separate_requirements))

    @staticmethod
    def _nominal_event_amount(
        inputs: CalculatorInputs,
        event: EventInput,
        amount: Decimal,
        month_index: int,
    ) -> Decimal:
        if event.amount_basis == AmountBasis.TODAY:
            return amount * _growth_factor(inputs.annual_inflation_rate, month_index)
        return amount

    @staticmethod
    def _validate(inputs: CalculatorInputs, events: Sequence[EventInput]) -> None:
        money = (
            inputs.desired_monthly_lifestyle,
            inputs.current_investable_assets,
            inputs.current_monthly_investment,
            inputs.current_monthly_income,
            inputs.current_monthly_expenses,
            inputs.post_freedom_monthly_income,
            inputs.emergency_savings,
        )
        if any(value < ZERO for value in money):
            raise CalculatorInputError(InputErrorCode.NEGATIVE_MONEY, "Money inputs cannot be negative.")
        if inputs.target_date <= inputs.calculation_date:
            raise CalculatorInputError(InputErrorCode.INVALID_DATE_RANGE, "Target month must follow calculation month.")
        if _months_between(inputs.calculation_date, inputs.target_date) > MAX_PROJECTION_MONTHS:
            raise CalculatorInputError(InputErrorCode.INVALID_DATE_RANGE, "Target exceeds the 100-year horizon.")
        if not ZERO < inputs.withdrawal_rate <= MAX_WITHDRAWAL_RATE:
            raise CalculatorInputError(
                InputErrorCode.INVALID_WITHDRAWAL_RATE,
                "Withdrawal rate must be greater than 0% and no more than 10%.",
            )
        rates = (
            inputs.annual_return_rate,
            inputs.annual_inflation_rate,
            inputs.annual_income_growth_rate,
            inputs.annual_contribution_growth_rate,
        )
        if any(rate < MIN_ANNUAL_RATE or rate > MAX_ANNUAL_RATE for rate in rates):
            raise CalculatorInputError(InputErrorCode.INVALID_RATE, "Annual rates must be between -99% and 100%.")
        if not ZERO <= inputs.safety_buffer_rate <= MAX_SAFETY_BUFFER_RATE:
            raise CalculatorInputError(InputErrorCode.INVALID_SAFETY_BUFFER, "Safety buffer must be 0% to 100%.")
        if len(events) > MAX_EVENTS:
            raise CalculatorInputError(InputErrorCode.TOO_MANY_EVENTS, "At most 50 events are supported.")
        for event in events:
            if not event.name or event.event_date <= inputs.calculation_date:
                raise CalculatorInputError(InputErrorCode.INVALID_EVENT, "Events need a name and a future month.")
            if event.one_time_amount < ZERO or event.recurring_monthly_amount < ZERO:
                raise CalculatorInputError(InputErrorCode.INVALID_EVENT, "Event amounts cannot be negative.")
            if event.one_time_amount == ZERO and event.recurring_monthly_amount == ZERO:
                raise CalculatorInputError(InputErrorCode.INVALID_EVENT, "An event must have a positive amount.")
            if event.recurring_monthly_amount and event.recurring_end_date is None:
                raise CalculatorInputError(InputErrorCode.INVALID_EVENT, "Recurring events require an end month.")
            if event.recurring_end_date and event.recurring_end_date < event.event_date:
                raise CalculatorInputError(
                    InputErrorCode.INVALID_EVENT, "Recurring end month cannot precede event month."
                )
            event_end = event.recurring_end_date or event.event_date
            if _months_between(inputs.calculation_date, event_end) > MAX_PROJECTION_MONTHS:
                raise CalculatorInputError(InputErrorCode.INVALID_EVENT, "Event exceeds the 100-year horizon.")

    @staticmethod
    def _annualize(rows: Sequence[MonthlyProjection]) -> tuple[AnnualProjection, ...]:
        grouped: dict[int, list[MonthlyProjection]] = {}
        for row in rows:
            grouped.setdefault(row.date.year, []).append(row)
        return tuple(
            AnnualProjection(
                year=year,
                opening_balance=year_rows[0].opening_balance,
                contributions=sum((row.contribution for row in year_rows), ZERO),
                growth=sum((row.growth for row in year_rows), ZERO),
                event_outflows=sum((row.event_outflow for row in year_rows), ZERO),
                event_shortfalls=sum((row.event_shortfall for row in year_rows), ZERO),
                closing_balance=year_rows[-1].closing_balance,
                target=year_rows[-1].target,
            )
            for year, year_rows in grouped.items()
        )

    @staticmethod
    def _warnings(
        inputs: CalculatorInputs,
        schedule: _EventSchedule,
        projection: Projection,
        base_case: CaseResult,
    ) -> tuple[WarningCode, ...]:
        warnings: list[WarningCode] = []
        if inputs.post_freedom_monthly_income >= inputs.desired_monthly_lifestyle:
            warnings.append(WarningCode.LIFESTYLE_FULLY_FUNDED)
        if inputs.current_monthly_investment > max(
            ZERO, inputs.current_monthly_income - inputs.current_monthly_expenses
        ):
            warnings.append(WarningCode.CONTRIBUTION_EXCEEDS_SURPLUS)
        if projection.total_event_shortfall:
            warnings.append(WarningCode.EVENT_SHORTFALL)
        if schedule.separate:
            warnings.append(WarningCode.SEPARATE_SAVINGS)
        if base_case.solver.status == SolverStatus.UNREACHABLE:
            warnings.append(WarningCode.SOLVER_UNREACHABLE)
        if base_case.achievement.status == AchievementStatus.NOT_REACHED:
            warnings.append(WarningCode.PACE_NOT_REACHED)
        return tuple(warnings)

    @staticmethod
    def _result_status(
        inputs: CalculatorInputs,
        current_target: TargetBreakdown,
        base_case: CaseResult,
    ) -> ResultStatus:
        if inputs.current_investable_assets >= current_target.total_target:
            return ResultStatus.COMPLETE
        if base_case.solver.status == SolverStatus.UNREACHABLE:
            return ResultStatus.UNREACHABLE
        if base_case.solver.monthly_contribution is not None and (
            inputs.current_monthly_investment >= base_case.solver.monthly_contribution
        ):
            return ResultStatus.ON_TRACK
        if base_case.achievement.status == AchievementStatus.NOT_REACHED:
            return ResultStatus.UNREACHABLE
        return ResultStatus.BEHIND


def _month_start(value: date) -> date:
    return value.replace(day=1)


def _months_between(start: date, end: date) -> int:
    return (end.year - start.year) * 12 + end.month - start.month


def _add_months(start: date, months: int) -> date:
    month_number = start.year * 12 + start.month - 1 + months
    return date(month_number // 12, month_number % 12 + 1, 1)


def _percentage(value: Decimal) -> Decimal:
    return value / ONE_HUNDRED


def _growth_factor(annual_rate: Decimal, months: int) -> Decimal:
    with localcontext() as context:
        context.prec = 40
        annual_factor = Decimal("1") + _percentage(annual_rate)
        return (annual_factor.ln() * Decimal(months) / Decimal("12")).exp()


def _annual_growth_factor(annual_rate: Decimal, years: int) -> Decimal:
    return (Decimal("1") + _percentage(annual_rate)) ** years


def _money(value: Decimal) -> str:
    return format(value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP), "f")


def _decimal_string(value: Decimal, quantum: Decimal) -> str:
    return format(value.quantize(quantum, rounding=ROUND_HALF_UP), "f")


def _target_payload(target: TargetBreakdown) -> dict[str, str]:
    return {
        "base_freedom_number_today": _money(target.base_freedom_number_today),
        "base_freedom_number_at_target": _money(target.base_freedom_number_at_target),
        "post_target_event_reserve": _money(target.post_target_event_reserve),
        "emergency_reserve": _money(target.emergency_reserve),
        "target_before_buffer": _money(target.target_before_buffer),
        "safety_buffer": _money(target.safety_buffer),
        "total_target": _money(target.total_target),
    }


def _monthly_payload(row: MonthlyProjection) -> dict[str, str]:
    return {
        "date": row.date.isoformat(),
        "opening_balance": _money(row.opening_balance),
        "contribution": _money(row.contribution),
        "growth": _money(row.growth),
        "event_outflow": _money(row.event_outflow),
        "event_shortfall": _money(row.event_shortfall),
        "closing_balance": _money(row.closing_balance),
        "target": _money(row.target),
    }


def _annual_payload(row: AnnualProjection) -> dict[str, str | int]:
    return {
        "year": row.year,
        "opening_balance": _money(row.opening_balance),
        "contributions": _money(row.contributions),
        "growth": _money(row.growth),
        "event_outflows": _money(row.event_outflows),
        "event_shortfalls": _money(row.event_shortfalls),
        "closing_balance": _money(row.closing_balance),
        "target": _money(row.target),
    }


def _case_payload(case: CaseResult) -> dict[str, object]:
    return {
        "annual_return_rate": _decimal_string(case.annual_return_rate, Decimal("0.001")),
        "withdrawal_rate": _decimal_string(case.withdrawal_rate, Decimal("0.001")),
        "target_breakdown": _target_payload(case.target),
        "required_contribution": {
            "status": case.solver.status,
            "monthly_contribution": (
                _money(case.solver.monthly_contribution) if case.solver.monthly_contribution is not None else None
            ),
            "iterations": case.solver.iterations,
        },
        "achievement": {
            "status": case.achievement.status,
            "date": case.achievement.achievement_date.isoformat() if case.achievement.achievement_date else None,
            "month_index": case.achievement.month_index,
        },
    }
