from __future__ import annotations

import calendar
from datetime import date, timedelta
from decimal import Decimal
from typing import Iterable, TypedDict

from django.contrib.auth.models import User
from django.db import transaction as db_transaction
from django.db.models import Case, DecimalField, F, Q, Sum, Value, When
from django.db.models.functions import Coalesce, TruncMonth

from .models import (
    BudgetAllocation,
    BudgetPeriod,
    Category,
    FinancialAccount,
    LedgerTransaction,
    LedgerTransactionQuerySet,
    first_of_month,
)

ZERO = Decimal("0.00")

DEFAULT_EXPENSE_CATEGORIES: tuple[tuple[str, str, str], ...] = (
    ("Food & dining", "utensils", "#c2410c"),
    ("Groceries", "basket", "#15803d"),
    ("Transport", "bus", "#1d4ed8"),
    ("Housing", "home", "#7c3aed"),
    ("Utilities", "bolt", "#0e7490"),
    ("Health", "heart", "#be123c"),
    ("Education", "book", "#4338ca"),
    ("Shopping", "bag", "#a16207"),
    ("Entertainment", "film", "#9333ea"),
    ("Savings & investment", "piggy-bank", "#047857"),
    ("Other", "ellipsis", "#525252"),
)

DEFAULT_INCOME_CATEGORIES: tuple[tuple[str, str, str], ...] = (
    ("Salary", "wallet", "#047857"),
    ("Bonus", "gift", "#0f766e"),
    ("Side income", "briefcase", "#1d4ed8"),
    ("Investment income", "chart-line", "#7c3aed"),
    ("Other income", "ellipsis", "#525252"),
)


class MonthTotals(TypedDict):
    income: Decimal
    expense: Decimal
    net: Decimal


def month_range(month: date) -> tuple[date, date]:
    """Return the inclusive first and last day of the month `month` falls in."""

    start = first_of_month(month)
    last_day = calendar.monthrange(start.year, start.month)[1]
    return start, start.replace(day=last_day)


def add_months(month: date, offset: int) -> date:
    """Return the first day of the month `offset` months away from `month`."""

    start = first_of_month(month)
    total = start.year * 12 + (start.month - 1) + offset
    return date(total // 12, total % 12 + 1, 1)


def months_between(start: date, end: date) -> list[date]:
    """Return the first day of every month from `start` to `end`, inclusive."""

    current, last = first_of_month(start), first_of_month(end)
    months: list[date] = []
    while current <= last:
        months.append(current)
        current = add_months(current, 1)
    return months


def ensure_default_categories(user: User) -> None:
    """Seed a starter set of categories the first time a user needs them.

    Does nothing once the user owns any category, so a user who deleted or archived
    the defaults does not get them back.
    """

    if Category.objects.filter(user=user).exists():
        return

    rows = [
        Category(user=user, type=category_type, name=name, icon=icon, color=color, sort_order=index)
        for category_type, defaults in (
            (Category.Type.EXPENSE, DEFAULT_EXPENSE_CATEGORIES),
            (Category.Type.INCOME, DEFAULT_INCOME_CATEGORIES),
        )
        for index, (name, icon, color) in enumerate(defaults)
    ]
    Category.objects.bulk_create(rows)


def active_transactions(user: User) -> LedgerTransactionQuerySet:
    """Return the user's transactions that count towards every list and total."""

    return LedgerTransaction.objects.for_user(user).active()


def account_balances(user: User, *, as_of: date | None = None) -> dict[int, Decimal]:
    """Return the derived balance of every account the user owns, keyed by account id.

    A balance is the opening balance plus incoming money (income and transfers in)
    minus outgoing money (expenses and transfers out). Transfers move balance between
    two accounts and never count as income or expense.
    """

    transactions = active_transactions(user)
    if as_of is not None:
        transactions = transactions.filter(occurred_on__lte=as_of)

    outgoing = dict(
        transactions.filter(type__in=(LedgerTransaction.Type.EXPENSE, LedgerTransaction.Type.TRANSFER))
        .values_list("account")
        .annotate(total=Sum("amount"))
    )
    incoming_income = dict(
        transactions.filter(type=LedgerTransaction.Type.INCOME).values_list("account").annotate(total=Sum("amount"))
    )
    incoming_transfer = dict(
        transactions.filter(type=LedgerTransaction.Type.TRANSFER)
        .values_list("transfer_account")
        .annotate(total=Sum("amount"))
    )

    balances: dict[int, Decimal] = {}
    for account_id, opening_balance, opening_balance_date in FinancialAccount.objects.for_user(user).values_list(
        "id", "opening_balance", "opening_balance_date"
    ):
        balance = opening_balance if as_of is None or opening_balance_date <= as_of else ZERO
        balance += incoming_income.get(account_id, ZERO) + incoming_transfer.get(account_id, ZERO)
        balance -= outgoing.get(account_id, ZERO)
        balances[account_id] = balance
    return balances


def month_totals(user: User, start: date, end: date) -> MonthTotals:
    """Return income, expense, and net totals for an inclusive date range."""

    totals = (
        active_transactions(user)
        .in_range(start, end)
        .aggregate(
            income=Coalesce(Sum("amount", filter=Q(type=LedgerTransaction.Type.INCOME)), Value(ZERO)),
            expense=Coalesce(Sum("amount", filter=Q(type=LedgerTransaction.Type.EXPENSE)), Value(ZERO)),
        )
    )
    return MonthTotals(income=totals["income"], expense=totals["expense"], net=totals["income"] - totals["expense"])


def category_actuals(
    user: User, start: date, end: date, category_type: str = Category.Type.EXPENSE
) -> dict[int, Decimal]:
    """Return the spent (or earned) total per category id for an inclusive date range."""

    transaction_type = (
        LedgerTransaction.Type.EXPENSE if category_type == Category.Type.EXPENSE else LedgerTransaction.Type.INCOME
    )
    return dict(
        active_transactions(user)
        .in_range(start, end)
        .filter(type=transaction_type, category__isnull=False)
        .values_list("category")
        .annotate(total=Sum("amount"))
    )


def category_actuals_by_month(
    user: User, start: date, end: date, category_type: str = Category.Type.EXPENSE
) -> dict[int, dict[date, Decimal]]:
    """Return the per-month total of every category inside an inclusive date range."""

    transaction_type = (
        LedgerTransaction.Type.EXPENSE if category_type == Category.Type.EXPENSE else LedgerTransaction.Type.INCOME
    )
    rows = (
        active_transactions(user)
        .in_range(start, end)
        .filter(type=transaction_type, category__isnull=False)
        .annotate(month=TruncMonth("occurred_on"))
        .values_list("category", "month")
        .annotate(total=Sum("amount"))
    )
    months = months_between(start, end)
    trends: dict[int, dict[date, Decimal]] = {}
    for category_id, month, total in rows:
        trends.setdefault(category_id, dict.fromkeys(months, ZERO))[month] = total
    return trends


def monthly_totals(user: User, start: date, end: date) -> dict[date, MonthTotals]:
    """Return income, expense, and net totals per month across an inclusive range."""

    rows = (
        active_transactions(user)
        .in_range(start, end)
        .annotate(month=TruncMonth("occurred_on"))
        .values("month")
        .annotate(
            income=Coalesce(Sum("amount", filter=Q(type=LedgerTransaction.Type.INCOME)), Value(ZERO)),
            expense=Coalesce(Sum("amount", filter=Q(type=LedgerTransaction.Type.EXPENSE)), Value(ZERO)),
        )
    )
    totals = {
        row["month"]: MonthTotals(income=row["income"], expense=row["expense"], net=row["income"] - row["expense"])
        for row in rows
    }
    return {
        month: totals.get(month, MonthTotals(income=ZERO, expense=ZERO, net=ZERO))
        for month in months_between(start, end)
    }


def percentage_used(planned: Decimal, actual: Decimal) -> Decimal:
    """Return how much of `planned` has been used, as a percentage with one decimal."""

    if planned <= ZERO:
        return ZERO
    return (actual / planned * 100).quantize(Decimal("0.1"))


def get_or_create_period(user: User, month: date) -> BudgetPeriod:
    """Return the user's budget period for `month`, creating an open one when missing."""

    period, _ = BudgetPeriod.objects.get_or_create(user=user, month=first_of_month(month))
    return period


def get_period(user: User, month: date) -> BudgetPeriod:
    """Return the user's budget period for `month`, unsaved when the month has no budget.

    Reading a month must not write one, so a month nobody has budgeted yet is reported
    through a transient period that behaves like an open month with no allocations.
    """

    normalised = first_of_month(month)
    period = BudgetPeriod.objects.filter(user=user, month=normalised).first()
    return period or BudgetPeriod(user=user, month=normalised)


def budget_detail(user: User, month: date) -> dict:
    """Return one budgeted month: its allocations, their actuals, and unbudgeted spending.

    Actuals are always computed from the transactions themselves. Spending in a category
    with no allocation (including an archived category) lands in the unbudgeted bucket so
    the month's totals always reconcile with the transaction list.
    """

    period = get_period(user, month)
    start, end = month_range(period.month)
    actuals = category_actuals(user, start, end)

    allocations = []
    planned_total = ZERO
    allocated_actual_total = ZERO
    stored_allocations = period.allocations.select_related("category") if period.pk else []
    for allocation in stored_allocations:
        actual = actuals.pop(allocation.category_id, ZERO)
        planned_total += allocation.planned_amount
        allocated_actual_total += actual
        allocations.append(
            {
                "allocation": allocation,
                "planned": allocation.planned_amount,
                "actual": actual,
                "remaining": allocation.planned_amount - actual,
                "percentage_used": percentage_used(allocation.planned_amount, actual),
            }
        )

    unbudgeted_categories = {
        category.id: category for category in Category.objects.for_user(user).filter(id__in=actuals.keys())
    }
    unbudgeted = [
        {"category": unbudgeted_categories[category_id], "actual": actual}
        for category_id, actual in actuals.items()
        if category_id in unbudgeted_categories
    ]
    totals = month_totals(user, start, end)
    unbudgeted_total = sum((row["actual"] for row in unbudgeted), ZERO)

    return {
        "period": period,
        "start": start,
        "end": end,
        "allocations": allocations,
        "unbudgeted": {"total": unbudgeted_total, "categories": unbudgeted},
        "totals": {
            "planned": planned_total,
            "actual": totals["expense"],
            "allocated_actual": allocated_actual_total,
            "remaining": planned_total - allocated_actual_total,
            "percentage_used": percentage_used(planned_total, allocated_actual_total),
            "income": totals["income"],
            "net": totals["net"],
        },
    }


@db_transaction.atomic
def copy_allocations(user: User, target: BudgetPeriod, source_month: date) -> int:
    """Copy the planned amounts of `source_month` onto `target`, never the actuals.

    Running it twice with the same source leaves the same allocations, so a retry can
    never double a budget.
    """

    source = BudgetPeriod.objects.filter(user=user, month=first_of_month(source_month)).first()
    if source is None:
        return 0

    copied = 0
    for allocation in source.allocations.all():
        _, created = BudgetAllocation.objects.update_or_create(
            period=target,
            category=allocation.category,
            defaults={"planned_amount": allocation.planned_amount},
        )
        copied += 1 if created else 0
    return copied


def account_movement(user: User, start: date, end: date) -> dict[int, dict[str, Decimal]]:
    """Return money in, money out, and the net change per account over a date range."""

    transactions = active_transactions(user).in_range(start, end)
    outgoing = dict(
        transactions.filter(type__in=(LedgerTransaction.Type.EXPENSE, LedgerTransaction.Type.TRANSFER))
        .values_list("account")
        .annotate(total=Sum("amount"))
    )
    incoming = dict(
        transactions.filter(type=LedgerTransaction.Type.INCOME).values_list("account").annotate(total=Sum("amount"))
    )
    transfers_in = dict(
        transactions.filter(type=LedgerTransaction.Type.TRANSFER)
        .values_list("transfer_account")
        .annotate(total=Sum("amount"))
    )

    movement: dict[int, dict[str, Decimal]] = {}
    for account_id in FinancialAccount.objects.for_user(user).values_list("id", flat=True):
        money_in = incoming.get(account_id, ZERO) + transfers_in.get(account_id, ZERO)
        money_out = outgoing.get(account_id, ZERO)
        movement[account_id] = {"money_in": money_in, "money_out": money_out, "net": money_in - money_out}
    return movement


def _net_change(user: User, account_ids: list[int], start: date | None, end: date) -> Decimal:
    """Return how much the given accounts gained or lost up to, or within, a date range."""

    transactions = active_transactions(user).filter(occurred_on__lte=end)
    if start is not None:
        transactions = transactions.filter(occurred_on__gte=start)

    money_in = transactions.filter(
        Q(type=LedgerTransaction.Type.INCOME, account_id__in=account_ids)
        | Q(type=LedgerTransaction.Type.TRANSFER, transfer_account_id__in=account_ids)
    ).aggregate(total=Coalesce(Sum("amount"), Value(ZERO)))["total"]
    money_out = transactions.filter(
        type__in=(LedgerTransaction.Type.EXPENSE, LedgerTransaction.Type.TRANSFER), account_id__in=account_ids
    ).aggregate(total=Coalesce(Sum("amount"), Value(ZERO)))["total"]
    return money_in - money_out


def net_worth_series(user: User, months: Iterable[date]) -> list[dict]:
    """Return the closing balance of the budget-included accounts for each month.

    A transfer between two included accounts cancels itself out; one that leaves for an
    excluded account lowers net worth, which is what makes a card payment show up.
    """

    requested = [first_of_month(month) for month in months]
    if not requested:
        return []

    accounts = list(
        FinancialAccount.objects.for_user(user)
        .filter(include_in_budget=True)
        .values_list("id", "opening_balance", "opening_balance_date")
    )
    if not accounts:
        return [{"month": month, "balance": ZERO} for month in requested]

    account_ids = [account_id for account_id, _, _ in accounts]
    first_month, last_month = requested[0], requested[-1]

    openings: dict[date, Decimal] = {}
    balance = ZERO
    for _, opening_balance, opening_date in accounts:
        if opening_date < first_month:
            balance += opening_balance
        else:
            month = first_of_month(opening_date)
            openings[month] = openings.get(month, ZERO) + opening_balance

    # Everything that happened before the first requested month collapses into one number.
    balance += _net_change(user, account_ids, None, first_month - timedelta(days=1))

    changes = dict(
        active_transactions(user)
        .in_range(first_month, month_range(last_month)[1])
        .filter(
            Q(type=LedgerTransaction.Type.INCOME, account_id__in=account_ids)
            | Q(type=LedgerTransaction.Type.TRANSFER, transfer_account_id__in=account_ids)
            | Q(type=LedgerTransaction.Type.EXPENSE, account_id__in=account_ids)
            | Q(type=LedgerTransaction.Type.TRANSFER, account_id__in=account_ids)
        )
        .annotate(month=TruncMonth("occurred_on"))
        .values_list("month")
        .annotate(
            total=Coalesce(
                Sum(
                    Case(
                        When(
                            Q(type=LedgerTransaction.Type.INCOME, account_id__in=account_ids)
                            | Q(type=LedgerTransaction.Type.TRANSFER, transfer_account_id__in=account_ids),
                            then=F("amount"),
                        ),
                        default=Value(ZERO),
                        output_field=DecimalField(max_digits=15, decimal_places=2),
                    )
                )
                - Sum(
                    Case(
                        When(
                            Q(
                                type__in=(LedgerTransaction.Type.EXPENSE, LedgerTransaction.Type.TRANSFER),
                                account_id__in=account_ids,
                            ),
                            then=F("amount"),
                        ),
                        default=Value(ZERO),
                        output_field=DecimalField(max_digits=15, decimal_places=2),
                    )
                ),
                Value(ZERO),
            )
        )
    )

    series = []
    for month in requested:
        balance += openings.get(month, ZERO) + changes.get(month, ZERO)
        series.append({"month": month, "balance": balance})
    return series
