from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from prospectus_lumos.apps.financial_planning.models import FinancialEvent, FreedomScenario

MONEY_FIELDS: tuple[tuple[str, str], ...] = (
    ("desired_monthly_lifestyle", "Monthly lifestyle you want"),
    ("post_freedom_monthly_income", "Monthly income that continues"),
    ("current_investable_assets", "Money already invested"),
    ("current_monthly_investment", "Invested each month today"),
    ("current_monthly_income", "Monthly take-home income"),
    ("current_monthly_expenses", "Monthly spending"),
    ("emergency_savings", "Emergency savings"),
)

RATE_FIELDS: tuple[tuple[str, str], ...] = (
    ("annual_return_rate", "Yearly investment growth"),
    ("annual_inflation_rate", "Yearly price rises"),
    ("withdrawal_rate", "Yearly spending from the fund"),
    ("safety_buffer_rate", "Extra cushion"),
    ("annual_income_growth_rate", "Yearly income growth"),
    ("annual_contribution_growth_rate", "Yearly growth in investing"),
)


@dataclass(frozen=True, slots=True)
class ComparisonRow:
    """One labelled difference between two saved versions."""

    label: str
    left: Any
    right: Any
    kind: str  # "money", "rate", "date", "text"
    changed: bool
    direction: str = "neutral"  # "better", "worse", "neutral"
    note: str = ""


def _rows(
    left: FreedomScenario, right: FreedomScenario, fields: tuple[tuple[str, str], ...], kind: str
) -> list[ComparisonRow]:
    rows: list[ComparisonRow] = []
    for field_name, label in fields:
        left_value = getattr(left, field_name)
        right_value = getattr(right, field_name)
        rows.append(
            ComparisonRow(
                label=label,
                left=left_value,
                right=right_value,
                kind=kind,
                changed=left_value != right_value,
            )
        )
    return rows


def _date_direction(left: date | None, right: date | None) -> tuple[str, str]:
    """Explain a moved freedom date. Earlier is favourable, later is not."""

    if left == right:
        return "neutral", ""
    if right is None:
        return "worse", "The newer version does not reach the target inside this projection."
    if left is None:
        return "better", "The newer version reaches the target, where the older one did not."
    if right < left:
        return "better", "The newer version reaches the target earlier."
    return "worse", "The newer version reaches the target later."


def _money_direction(left: Decimal | None, right: Decimal | None, *, less_is_better: bool) -> str:
    if left == right:
        return "neutral"
    if left is None or right is None:
        return "neutral"
    if right < left:
        return "better" if less_is_better else "worse"
    return "worse" if less_is_better else "better"


def _output_rows(left: FreedomScenario, right: FreedomScenario) -> list[ComparisonRow]:
    date_direction, date_note = _date_direction(left.projected_achievement_date, right.projected_achievement_date)
    required_note = ""
    if left.required_monthly_investment is None or right.required_monthly_investment is None:
        required_note = "One of these versions falls outside the range of monthly amounts the estimate can solve."
    return [
        ComparisonRow(
            label="Total target",
            left=left.total_target,
            right=right.total_target,
            kind="money",
            changed=left.total_target != right.total_target,
            note="A bigger target is not automatically worse — it usually means a bigger goal.",
        ),
        ComparisonRow(
            label="Suggested each month",
            left=left.required_monthly_investment,
            right=right.required_monthly_investment,
            kind="money",
            changed=left.required_monthly_investment != right.required_monthly_investment,
            direction=_money_direction(
                left.required_monthly_investment, right.required_monthly_investment, less_is_better=True
            ),
            note=required_note,
        ),
        ComparisonRow(
            label="Date at your current pace",
            left=left.projected_achievement_date,
            right=right.projected_achievement_date,
            kind="date",
            changed=left.projected_achievement_date != right.projected_achievement_date,
            direction=date_direction,
            note=date_note,
        ),
        ComparisonRow(
            label="Monthly shortfall",
            left=left.funding_gap,
            right=right.funding_gap,
            kind="money",
            changed=left.funding_gap != right.funding_gap,
            direction=_money_direction(left.funding_gap, right.funding_gap, less_is_better=True),
        ),
        ComparisonRow(
            label="Freedom date you chose",
            left=left.target_date,
            right=right.target_date,
            kind="date",
            changed=left.target_date != right.target_date,
        ),
        ComparisonRow(
            label="Where it stands",
            left=left.get_result_status_display(),
            right=right.get_result_status_display(),
            kind="text",
            changed=left.result_status != right.result_status,
        ),
    ]


def _event_key(event: FinancialEvent) -> tuple[str, date, int]:
    return event.name.strip().lower(), event.event_date, event.sort_order


def _event_summary(event: FinancialEvent) -> dict[str, Any]:
    return {
        "name": event.name,
        "event_date": event.event_date,
        "one_time_amount": event.one_time_amount,
        "recurring_monthly_amount": event.recurring_monthly_amount,
        "recurring_end_date": event.recurring_end_date,
        "funding_label": event.get_funding_source_display(),
        "funding_source": event.funding_source,
    }


def compare_events(left: FreedomScenario, right: FreedomScenario) -> dict[str, list[dict[str, Any]]]:
    """Group the plans of both versions into added, removed, changed, and unchanged."""

    left_events = {_event_key(event): event for event in left.events.all()}
    right_events = {_event_key(event): event for event in right.events.all()}
    added = [_event_summary(event) for key, event in right_events.items() if key not in left_events]
    removed = [_event_summary(event) for key, event in left_events.items() if key not in right_events]
    changed: list[dict[str, Any]] = []
    unchanged: list[dict[str, Any]] = []
    for key, right_event in right_events.items():
        left_event = left_events.get(key)
        if left_event is None:
            continue
        before = _event_summary(left_event)
        after = _event_summary(right_event)
        if before == after:
            unchanged.append(after)
        else:
            changed.append({"before": before, "after": after})
    return {"added": added, "removed": removed, "changed": changed, "unchanged": unchanged}


def compare_scenarios(left: FreedomScenario, right: FreedomScenario) -> dict[str, Any]:
    """Compare two saved versions of one plan without touching either of them."""

    groups: list[dict[str, Any]] = [
        {
            "title": "What changed in your answers",
            "description": "The amounts and dates you entered.",
            "rows": _rows(left, right, MONEY_FIELDS, "money"),
        },
        {
            "title": "What changed in the assumptions",
            "description": "The rates each version was estimated with.",
            "rows": _rows(left, right, RATE_FIELDS, "rate"),
        },
        {
            "title": "What changed in the result",
            "description": "The numbers each version produced.",
            "rows": _output_rows(left, right),
        },
    ]
    events = compare_events(left, right)
    return {
        "left": left,
        "right": right,
        "groups": groups,
        "events": events,
        "event_changes": bool(events["added"] or events["removed"] or events["changed"]),
        "has_changes": any(row.changed for group in groups for row in group["rows"]),
    }
