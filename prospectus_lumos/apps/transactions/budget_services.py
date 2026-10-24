from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.db.models import Sum

from prospectus_lumos.apps.transactions.models import CategoryBudget, Transaction
from prospectus_lumos.core.categories import category_key


@dataclass(frozen=True)
class CategoryBudgetActual:
    """One category's optional limit and combined manual/imported expense actual."""

    category: str
    budget: Decimal | None
    actual: Decimal

    @property
    def remaining(self) -> Decimal | None:
        """Return limit minus actual, or None when no limit exists."""

        return None if self.budget is None else self.budget - self.actual

    @property
    def over(self) -> Decimal | None:
        """Return spending above the limit, or None when no limit exists."""

        return None if self.budget is None else max(self.actual - self.budget, Decimal("0"))


def get_monthly_budget_actuals(*, user: User, year: int, month: int) -> list[CategoryBudgetActual]:
    """Return budgeted and spent categories for one owner's document month.

    Uses exactly two queries, scoped to the user/year/month: one grouped expense
    aggregate and one limits query. Only grouped text categories are loaded, never
    individual transactions. Apply the shared key after grouping so all surrounding
    whitespace supported by Python's strip is handled without rewriting old labels.
    Dates, document summary totals, and transaction origins do not affect actuals.

    :param user: owner from trusted caller context.
    :param year: calendar year, 1-9999.
    :param month: calendar month, 1-12.
    :return: case-preserving categories sorted by label, including unbudgeted spending.
    :raises ValueError: if the calendar period is invalid.
    """

    date(year, month, 1)
    expenses = (
        Transaction.objects.filter(
            document__user=user,
            document__year=year,
            document__month=month,
            transaction_type=Transaction.TransactionType.EXPENSE,
        )
        .order_by()
        .values("category")
        .annotate(actual=Sum("amount"))
    )
    actuals: dict[str, Decimal] = {}
    for expense in expenses:
        key = category_key(expense["category"])
        actuals[key] = actuals.get(key, Decimal("0")) + expense["actual"]
    budgets = dict(CategoryBudget.objects.filter(user=user, year=year, month=month).values_list("category", "amount"))
    return [
        CategoryBudgetActual(category=key, budget=budgets.get(key), actual=actuals.get(key, Decimal("0")))
        for key in sorted(budgets.keys() | actuals.keys())
    ]


def upsert_category_budget(*, user: User, year: int, month: int, category: str, amount: Decimal) -> CategoryBudget:
    """Create or update one owner's normalized category-month limit atomically.

    :param user: owner from trusted caller context; no submitted budget ID is accepted.
    :param year: calendar year, 1-9999.
    :param month: calendar month, 1-12.
    :param category: case-preserving text label; blank maps to Uncategorized.
    :param amount: non-negative Decimal limit with model precision; zero is a limit.
    :return: the saved limit, preserving its ID when updated.
    :raises ValidationError: if any model field value is invalid.
    """

    candidate = CategoryBudget(user=user, year=year, month=month, category=category_key(category), amount=amount)
    candidate.full_clean(validate_unique=False, validate_constraints=False)
    budget, _ = CategoryBudget.objects.update_or_create(
        user=user,
        year=candidate.year,
        month=candidate.month,
        category=candidate.category,
        defaults={"amount": candidate.amount},
    )
    return budget


def remove_category_budget(*, user: User, year: int, month: int, category: str) -> bool:
    """Remove one owner's limit without changing spending or creating a document.

    :param user: owner from trusted caller context.
    :param year: calendar year, 1-9999.
    :param month: calendar month, 1-12.
    :param category: text label normalized with the shared category key.
    :return: True if a limit was removed; False if none existed (also on retries).
    :raises ValueError: if the calendar period is invalid.
    """

    date(year, month, 1)
    deleted, _ = CategoryBudget.objects.filter(
        user=user, year=year, month=month, category=category_key(category)
    ).delete()
    return deleted > 0
