from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.models import User

from prospectus_lumos.apps.ledger.models import Category, FinancialAccount, LedgerTransaction
from prospectus_lumos.apps.ledger.services import active_transactions, category_actuals, month_range, month_totals
from prospectus_lumos.core.utils import format_idr

PENDING_TOKEN_KEY = "transaction_form_pending_tokens"
USED_TOKEN_KEY = "transaction_form_used_tokens"


def recent_transaction_options(
    user: User,
    *,
    limit: int = 3,
) -> tuple[list[FinancialAccount], list[Category]]:
    """Return recently used active accounts and categories for income/expense entry."""

    recent_accounts: list[FinancialAccount] = []
    recent_categories: list[Category] = []
    seen_account_ids: set[int] = set()
    seen_category_ids: set[int] = set()
    transactions = (
        active_transactions(user)
        .exclude(type=LedgerTransaction.Type.TRANSFER)
        .select_related("account", "category")[:12]
    )
    for transaction in transactions:
        if transaction.account.archived_at is None and transaction.account_id not in seen_account_ids:
            recent_accounts.append(transaction.account)
            seen_account_ids.add(transaction.account_id)
        if (
            transaction.category
            and transaction.category.archived_at is None
            and transaction.category_id not in seen_category_ids
        ):
            recent_categories.append(transaction.category)
            seen_category_ids.add(transaction.category_id)
        if len(recent_accounts) >= limit and len(recent_categories) >= limit:
            break
    return recent_accounts[:limit], recent_categories[:limit]


def transaction_success_message(transaction: LedgerTransaction) -> str:
    """Describe a saved income or expense using canonical ledger totals."""

    start, end = month_range(transaction.occurred_on)
    if transaction.type == LedgerTransaction.Type.EXPENSE and transaction.category_id:
        actual = category_actuals(transaction.user, start, end).get(transaction.category_id, Decimal("0.00"))
        return f"Expense saved. {transaction.category.name} spending this month is now {format_idr(actual)}."
    income = month_totals(transaction.user, start, end)["income"]
    return f"Income saved. Income this month is now {format_idr(income)}."
