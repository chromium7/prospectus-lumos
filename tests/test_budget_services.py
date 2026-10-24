from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase

from prospectus_lumos.apps.documents.models import Document
from prospectus_lumos.apps.documents.services import resolve_monthly_document
from prospectus_lumos.apps.transactions.budget_services import (
    get_monthly_budget_actuals,
    remove_category_budget,
    upsert_category_budget,
)
from prospectus_lumos.apps.transactions.models import CategoryBudget, Transaction


class BudgetServiceTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(username="budget-owner")
        self.other = User.objects.create_user(username="other-owner")
        self.document = resolve_monthly_document(user=self.user, year=2026, month=10)

    def _expense(self, category: str = "Food", **overrides: object) -> Transaction:
        values = {
            "document": self.document,
            "transaction_type": Transaction.TransactionType.EXPENSE,
            "date": "old sheet date",
            "amount": Decimal("10.25"),
            "description": "Expense",
            "category": category,
            "origin": Transaction.Origin.GOOGLE_SHEETS,
        }
        values.update(overrides)
        return Transaction.objects.create(**values)  # type: ignore[arg-type]

    def _budget(self, category: str = "Food", amount: str = "50.00", **overrides: object) -> CategoryBudget:
        values = {"user": self.user, "year": 2026, "month": 10, "category": category, "amount": Decimal(amount)}
        values.update(overrides)
        return upsert_category_budget(**values)  # type: ignore[arg-type]

    def test_actuals_use_document_month_and_all_origins_but_exclude_income_and_other_periods(self) -> None:
        self._budget()
        self._expense(user=self.user, date="2026-10-01", origin=Transaction.Origin.MANUAL)
        self._expense(date="1/10/2026")
        self._expense(date="", origin=Transaction.Origin.DIRECT_UPLOAD)
        self._expense(transaction_type=Transaction.TransactionType.INCOME, amount=Decimal("999"))
        self._expense(document=resolve_monthly_document(user=self.user, year=2026, month=9))
        self._expense(document=resolve_monthly_document(user=self.user, year=2025, month=10))
        self._expense(document=resolve_monthly_document(user=self.other, year=2026, month=10), user=self.user)
        self._expense(document=None, user=self.user, date="2026-10-01")
        with self.assertNumQueries(2):
            rows = get_monthly_budget_actuals(user=self.user, year=2026, month=10)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].category, "Food")
        self.assertEqual(rows[0].actual, Decimal("30.75"))
        self.assertEqual(rows[0].remaining, Decimal("19.25"))
        self.assertEqual(rows[0].over, Decimal("0"))

    def test_category_matching_preserves_historical_rows_case_and_internal_whitespace(self) -> None:
        self._budget("\tFood\n", "10")
        self._budget("", "0")
        historical = self._expense("\tFood\n")
        self._expense("Food")
        self._expense("food")
        self._expense("Food  and\tDrink")
        self._expense("")
        self._expense(" \t\n")
        self._expense(" Uncategorized ")
        rows = {row.category: row for row in get_monthly_budget_actuals(user=self.user, year=2026, month=10)}
        self.assertEqual(set(rows), {"Food", "food", "Food  and\tDrink", "Uncategorized"})
        self.assertEqual(rows["Food"].actual, Decimal("20.50"))
        self.assertEqual(rows["Food"].remaining, Decimal("-10.50"))
        self.assertEqual(rows["Food"].over, Decimal("10.50"))
        self.assertEqual(rows["Uncategorized"].actual, Decimal("30.75"))
        historical.refresh_from_db()
        self.assertEqual(historical.category, "\tFood\n")

    def test_no_limit_zero_limit_and_budget_without_spending_are_distinct(self) -> None:
        self._expense("No limit")
        self._expense("Zero")
        self._budget("Zero", "0")
        self._budget("Unused", "50")
        rows = {row.category: row for row in get_monthly_budget_actuals(user=self.user, year=2026, month=10)}
        self.assertIsNone(rows["No limit"].budget)
        self.assertIsNone(rows["No limit"].remaining)
        self.assertIsNone(rows["No limit"].over)
        self.assertEqual(rows["Zero"].budget, Decimal("0"))
        self.assertEqual(rows["Zero"].remaining, Decimal("-10.25"))
        self.assertEqual(rows["Zero"].over, Decimal("10.25"))
        self.assertEqual(rows["Unused"].actual, Decimal("0"))
        self.assertEqual(rows["Unused"].remaining, Decimal("50"))
        with self.assertNumQueries(2):
            self.assertEqual(get_monthly_budget_actuals(user=self.user, year=1, month=1), [])

    def test_upsert_and_remove_are_owner_period_and_category_scoped(self) -> None:
        expense = self._expense()
        budget = self._budget(" Food ")
        updated = self._budget(amount="0")
        self.assertEqual(updated.pk, budget.pk)
        self.assertEqual(updated.amount, Decimal("0"))
        other = self._budget(user=self.other)
        next_month = self._budget(month=11)
        next_year = self._budget(year=2027)
        other_category = self._budget("food")
        self.assertTrue(remove_category_budget(user=self.user, year=2026, month=10, category="\tFood\n"))
        self.assertFalse(remove_category_budget(user=self.user, year=2026, month=10, category="Food"))
        self.assertCountEqual(
            CategoryBudget.objects.values_list("pk", flat=True),
            [other.pk, next_month.pk, next_year.pk, other_category.pk],
        )
        with self.assertNumQueries(2):
            rows = get_monthly_budget_actuals(user=self.user, year=2026, month=10)
        self.assertEqual(rows[0].category, "Food")
        self.assertIsNone(rows[0].budget)
        expense.refresh_from_db()
        self.assertEqual(expense.amount, Decimal("10.25"))
        self._budget("", "0")
        self.assertTrue(remove_category_budget(user=self.user, year=2026, month=10, category="Uncategorized"))
        self._budget(year=9999, month=12)
        self.assertFalse(Document.objects.filter(user=self.user, year=9999, month=12).exists())

    def test_invalid_limits_do_not_replace_existing_budget(self) -> None:
        budget = self._budget()
        for overrides in (
            {"amount": "-0.01"},
            {"amount": "0.001"},
            {"amount": "10000000000000.00"},
            {"amount": "NaN"},
            {"amount": "Infinity"},
            {"category": "x" * 101},
            {"year": 0},
            {"year": 10000},
            {"month": 0},
            {"month": 13},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(ValidationError):
                self._budget(**overrides)
        budget.refresh_from_db()
        self.assertEqual(budget.amount, Decimal("50"))
        self.assertEqual(CategoryBudget.objects.count(), 1)
        with self.assertRaises(ValueError):
            get_monthly_budget_actuals(user=self.user, year=2026, month=13)
        with self.assertRaises(ValueError):
            remove_category_budget(user=self.user, year=0, month=10, category="Food")
