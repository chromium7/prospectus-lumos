from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from prospectus_lumos.apps.financial_planning.models import FinancialEvent, FreedomScenario

CASE_ORDER = ("conservative", "base", "optimistic")

CASE_COPY: dict[str, dict[str, str]] = {
    "conservative": {
        "label": "If things go slowly",
        "description": "Lower investment growth and a more careful yearly withdrawal.",
    },
    "base": {
        "label": "If things go as assumed",
        "description": "The assumptions saved with this version.",
    },
    "optimistic": {
        "label": "If things go well",
        "description": "Higher investment growth than assumed.",
    },
}

STATUS_COPY: dict[str, dict[str, str]] = {
    FreedomScenario.ResultStatus.COMPLETE: {
        "label": "Already there",
        "detail": "What you have saved already covers this target.",
        "badge": "text-bg-success",
        "icon": "bi-check-circle-fill",
    },
    FreedomScenario.ResultStatus.ON_TRACK: {
        "label": "On track",
        "detail": "Keeping up your current monthly investing reaches this target in time.",
        "badge": "text-bg-success",
        "icon": "bi-check-circle-fill",
    },
    FreedomScenario.ResultStatus.BEHIND: {
        "label": "Needs more each month",
        "detail": "At today's pace this target arrives later than the date you chose.",
        "badge": "text-bg-warning",
        "icon": "bi-exclamation-triangle-fill",
    },
    FreedomScenario.ResultStatus.UNREACHABLE: {
        "label": "Out of reach as set up",
        "detail": "No monthly amount inside the estimate's range reaches this target by that date.",
        "badge": "text-bg-danger",
        "icon": "bi-x-circle-fill",
    },
}

WARNING_COPY: dict[str, str] = {
    "lifestyle_fully_funded_by_income": "Your expected monthly income already covers the lifestyle you described.",
    "contribution_exceeds_monthly_surplus": (
        "The suggested monthly investment is more than the money left over each month today."
    ),
    "portfolio_event_shortfall": "At least one plan costs more than the investments available in that month.",
    "separate_savings_required": "Some plans are funded separately, so you need to save for them outside investments.",
    "unreachable_within_solver_bounds": "This target is outside the range of monthly amounts the estimate can solve.",
    "not_reached_within_horizon": "At today's pace this target is not reached inside the projection horizon.",
}

TARGET_BREAKDOWN_ROWS: tuple[tuple[str, str, str], ...] = (
    (
        "base_freedom_number_at_target",
        "Cost of your lifestyle, at that time",
        "Your monthly lifestyle grown for inflation, turned into the fund it needs.",
    ),
    (
        "post_target_event_reserve",
        "Set aside for later plans",
        "Money reserved for plans that happen after your freedom date.",
    ),
    (
        "emergency_reserve",
        "Emergency savings counted",
        "Only counted when you asked for emergency savings to be part of the goal.",
    ),
    ("safety_buffer", "Extra cushion", "A margin added on top so a bad stretch does not undo the plan."),
)

ASSUMPTION_ROWS: tuple[tuple[str, str, str], ...] = (
    ("annual_return_rate", "Yearly investment growth", "How fast the investments are assumed to grow."),
    ("annual_inflation_rate", "Yearly price rises", "How fast the cost of your lifestyle is assumed to rise."),
    ("withdrawal_rate", "Yearly spending from the fund", "The share of the fund assumed to be spent each year."),
    ("safety_buffer_rate", "Extra cushion", "Added on top of the target for safety."),
    ("annual_income_growth_rate", "Yearly income growth", "Applied to the income you earn today."),
    ("annual_contribution_growth_rate", "Yearly growth in investing", "Applied to what you invest each month."),
)


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


def _iso_to_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _case_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Return conservative/base/optimistic rows without any probability claim."""

    cases = payload.get("cases") or {}
    rows: list[dict[str, Any]] = []
    for name in CASE_ORDER:
        case = cases.get(name)
        if not isinstance(case, dict):
            continue
        required = case.get("required_contribution") or {}
        achievement = case.get("achievement") or {}
        rows.append(
            {
                "name": name,
                "label": CASE_COPY[name]["label"],
                "description": CASE_COPY[name]["description"],
                "annual_return_rate": _decimal(case.get("annual_return_rate")),
                "withdrawal_rate": _decimal(case.get("withdrawal_rate")),
                "total_target": _decimal((case.get("target_breakdown") or {}).get("total_target")),
                "monthly_contribution": (
                    _decimal(required.get("monthly_contribution"))
                    if required.get("monthly_contribution") is not None
                    else None
                ),
                "achievement_date": _iso_to_date(achievement.get("date")),
            }
        )
    return rows


def _target_breakdown_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    breakdown = payload.get("target_breakdown") or {}
    rows: list[dict[str, Any]] = []
    for key, label, explanation in TARGET_BREAKDOWN_ROWS:
        amount = _decimal(breakdown.get(key))
        if amount == 0 and key in {"post_target_event_reserve", "emergency_reserve"}:
            continue
        rows.append({"key": key, "label": label, "explanation": explanation, "amount": amount})
    return rows


def _annual_rows(payload: dict[str, Any], *, target_year: int) -> list[dict[str, Any]]:
    """Return every projected year up to and including the target year."""

    rows: list[dict[str, Any]] = []
    for row in payload.get("annual") or []:
        if not isinstance(row, dict):
            continue
        year = row.get("year")
        if not isinstance(year, int) or year > target_year:
            continue
        closing = _decimal(row.get("closing_balance"))
        target = _decimal(row.get("target"))
        rows.append(
            {
                "year": year,
                "closing_balance": closing,
                "target": target,
                "contributions": _decimal(row.get("contributions")),
                "growth": _decimal(row.get("growth")),
                "event_outflows": _decimal(row.get("event_outflows")),
                "reaches_target": closing >= target > 0,
                "is_milestone": year % 5 == 0 or year == target_year,
            }
        )
    return rows


def _separate_savings(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in payload.get("separate_savings") or []:
        if not isinstance(item, dict):
            continue
        items.append(
            {
                "name": item.get("name", ""),
                "event_date": _iso_to_date(item.get("event_date")),
                "nominal_total": _decimal(item.get("nominal_total")),
                "monthly_funding_need": _decimal(item.get("monthly_funding_need")),
            }
        )
    return items


def _event_rows(scenario: FreedomScenario) -> list[dict[str, Any]]:
    """Describe the saved events in plain language, ordered as the user arranged them."""

    separate_needs = {item["name"]: item for item in _separate_savings(scenario.projection_data or {})}
    rows: list[dict[str, Any]] = []
    for event in scenario.events.all():
        from_portfolio = event.funding_source == FinancialEvent.FundingSource.INVESTMENT_PORTFOLIO
        rows.append(
            {
                "event": event,
                "from_portfolio": from_portfolio,
                "funding_label": ("Paid from investments" if from_portfolio else "Saved for separately"),
                "effect": (
                    "Lowers the investments available for your freedom date."
                    if from_portfolio
                    else "Does not touch the investments in this estimate."
                ),
                "separate_need": separate_needs.get(event.name),
            }
        )
    return rows


def _warning_rows(payload: dict[str, Any]) -> list[str]:
    return [WARNING_COPY[code] for code in payload.get("warnings") or [] if code in WARNING_COPY]


def _assumption_rows(scenario: FreedomScenario) -> list[dict[str, Any]]:
    return [
        {"label": label, "explanation": explanation, "value": getattr(scenario, field_name)}
        for field_name, label, explanation in ASSUMPTION_ROWS
    ]


def scenario_result_context(scenario: FreedomScenario) -> dict[str, Any]:
    """Build the saved-result view model from stored outputs only.

    Every number comes from the scenario row and its frozen ``projection_data`` payload, never from
    current documents or transactions, so a saved version keeps reading the same after a later sync.
    """

    payload: dict[str, Any] = scenario.projection_data or {}
    status = STATUS_COPY.get(scenario.result_status, STATUS_COPY[FreedomScenario.ResultStatus.BEHIND])
    required = scenario.required_monthly_investment
    monthly_increase = (
        max(Decimal("0"), required - scenario.current_monthly_investment) if required is not None else None
    )
    annual_rows = _annual_rows(payload, target_year=scenario.target_date.year)
    return {
        "status_copy": status,
        "required_monthly_investment": required,
        "monthly_increase": monthly_increase,
        "cases": _case_rows(payload),
        "target_breakdown": _target_breakdown_rows(payload),
        "annual_rows": annual_rows,
        "milestone_rows": [row for row in annual_rows if row["is_milestone"]],
        "event_rows": _event_rows(scenario),
        "separate_savings": _separate_savings(payload),
        "assumptions": _assumption_rows(scenario),
        "warnings": _warning_rows(payload),
        "schema_version": payload.get("schema_version"),
        "calculation_version": payload.get("calculation_version", scenario.calculation_version),
    }
