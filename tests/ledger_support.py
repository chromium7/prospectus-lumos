"""Shared fixtures for the ledger API tests."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from django.contrib.auth.models import User
from django.test import TestCase

from prospectus_lumos.apps.ledger.models import (
    BudgetAllocation,
    BudgetPeriod,
    Category,
    FinancialAccount,
    LedgerTransaction,
)


class LedgerTestCase(TestCase):
    """A signed-in owner, a second user to prove isolation, and small factories."""

    def setUp(self) -> None:
        self.owner = User.objects.create_user(username="ledger-owner", password="pass")
        self.other = User.objects.create_user(username="ledger-other", password="pass")
        self.client.force_login(self.owner)

    def make_account(self, user: User | None = None, **overrides: Any) -> FinancialAccount:
        """Create an account with sensible defaults."""

        values: dict[str, Any] = {
            "name": "Wallet",
            "type": FinancialAccount.Type.CASH,
            "opening_balance": Decimal("1000000.00"),
            "opening_balance_date": date(2026, 1, 1),
        }
        values.update(overrides)
        return FinancialAccount.objects.create(user=user or self.owner, **values)

    def make_category(self, user: User | None = None, **overrides: Any) -> Category:
        """Create a category with sensible defaults."""

        values: dict[str, Any] = {"name": "Food", "type": Category.Type.EXPENSE}
        values.update(overrides)
        return Category.objects.create(user=user or self.owner, **values)

    def make_transaction(self, user: User | None = None, **overrides: Any) -> LedgerTransaction:
        """Create a transaction with sensible defaults, filling in the rows it needs."""

        owner = user or self.owner
        values: dict[str, Any] = {
            "type": LedgerTransaction.Type.EXPENSE,
            "amount": Decimal("50000.00"),
            "occurred_on": date(2026, 9, 10),
        }
        values.update(overrides)
        if "account" not in values:
            values["account"] = self.make_account(owner)
        if values["type"] != LedgerTransaction.Type.TRANSFER and "category" not in values:
            values["category"] = self.make_category(owner, type=values["type"])
        return LedgerTransaction.objects.create(user=owner, **values)

    def make_allocation(self, category: Category, planned: str, month: date = date(2026, 9, 1)) -> BudgetAllocation:
        """Budget one category in one month for the signed-in owner."""

        period, _ = BudgetPeriod.objects.get_or_create(user=category.user, month=month)
        return BudgetAllocation.objects.create(period=period, category=category, planned_amount=Decimal(planned))

    def post_json(self, url: str, payload: dict) -> Any:
        """POST a JSON body the way every client does."""

        return self.client.post(url, data=payload, content_type="application/json")

    def patch_json(self, url: str, payload: dict) -> Any:
        """PATCH a JSON body the way every client does."""

        return self.client.patch(url, data=payload, content_type="application/json")

    def put_json(self, url: str, payload: dict) -> Any:
        """PUT a JSON body the way every client does."""

        return self.client.put(url, data=payload, content_type="application/json")
