from datetime import date
from decimal import Decimal

from django.db import transaction as db_transaction
from django.db.utils import IntegrityError
from django.test import TestCase

from prospectus_lumos.apps.ledger.models import BudgetPeriod, Category, FinancialAccount, LedgerTransaction
from prospectus_lumos.apps.ledger.services import (
    account_balances,
    ensure_default_categories,
    month_range,
    month_totals,
    months_between,
)

from .ledger_support import LedgerTestCase


class LedgerModelTests(LedgerTestCase):
    def test_transaction_constraints_reject_impossible_rows(self) -> None:
        account = self.make_account()
        other_account = self.make_account(name="Bank", type=FinancialAccount.Type.BANK)
        category = self.make_category()

        with self.assertRaises(IntegrityError), db_transaction.atomic():
            LedgerTransaction.objects.create(
                user=self.owner,
                type=LedgerTransaction.Type.EXPENSE,
                amount=Decimal("0.00"),
                occurred_on=date(2026, 9, 1),
                account=account,
                category=category,
            )

        with self.assertRaises(IntegrityError), db_transaction.atomic():
            LedgerTransaction.objects.create(
                user=self.owner,
                type=LedgerTransaction.Type.EXPENSE,
                amount=Decimal("1000.00"),
                occurred_on=date(2026, 9, 1),
                account=account,
            )

        with self.assertRaises(IntegrityError), db_transaction.atomic():
            LedgerTransaction.objects.create(
                user=self.owner,
                type=LedgerTransaction.Type.TRANSFER,
                amount=Decimal("1000.00"),
                occurred_on=date(2026, 9, 1),
                account=account,
                transfer_account=account,
            )

        transfer = LedgerTransaction.objects.create(
            user=self.owner,
            type=LedgerTransaction.Type.TRANSFER,
            amount=Decimal("1000.00"),
            occurred_on=date(2026, 9, 1),
            account=account,
            transfer_account=other_account,
        )
        self.assertIsNone(transfer.category)

    def test_category_names_are_unique_per_user_and_type_among_active_rows(self) -> None:
        first = self.make_category(name="Food")
        self.make_category(name="Food", type=Category.Type.INCOME)
        self.make_category(self.other, name="Food")

        with self.assertRaises(IntegrityError), db_transaction.atomic():
            self.make_category(name="Food")

        first.archive()
        self.assertIsNotNone(first.archived_at)
        self.assertEqual(self.make_category(name="Food").name, "Food")

    def test_budget_period_normalises_its_month_and_stays_unique(self) -> None:
        period = BudgetPeriod.objects.create(user=self.owner, month=date(2026, 9, 17))

        self.assertEqual(period.month, date(2026, 9, 1))
        with self.assertRaises(IntegrityError), db_transaction.atomic():
            BudgetPeriod.objects.create(user=self.owner, month=date(2026, 9, 30))

    def test_soft_delete_round_trips(self) -> None:
        transaction = self.make_transaction()

        transaction.soft_delete()
        self.assertTrue(transaction.is_deleted)
        self.assertFalse(LedgerTransaction.objects.for_user(self.owner).active().exists())

        transaction.restore()
        self.assertFalse(transaction.is_deleted)
        self.assertEqual(LedgerTransaction.objects.for_user(self.owner).active().count(), 1)


class LedgerServiceTests(LedgerTestCase):
    def test_balances_follow_income_expense_and_transfers_but_not_deleted_rows(self) -> None:
        wallet = self.make_account(name="Wallet", opening_balance=Decimal("1000000.00"))
        bank = self.make_account(name="Bank", type=FinancialAccount.Type.BANK, opening_balance=Decimal("0.00"))
        card = self.make_account(name="Card", type=FinancialAccount.Type.CREDIT, opening_balance=Decimal("0.00"))
        salary = self.make_category(name="Salary", type=Category.Type.INCOME)
        groceries = self.make_category(name="Groceries")

        self.make_transaction(
            type=LedgerTransaction.Type.INCOME, amount=Decimal("5000000.00"), account=bank, category=salary
        )
        self.make_transaction(amount=Decimal("200000.00"), account=wallet, category=groceries)
        # A credit-card purchase is an expense when it happens.
        self.make_transaction(amount=Decimal("300000.00"), account=card, category=groceries)
        # Paying the card is a transfer, so it never counts as expense.
        self.make_transaction(
            type=LedgerTransaction.Type.TRANSFER,
            amount=Decimal("300000.00"),
            account=bank,
            transfer_account=card,
        )
        deleted = self.make_transaction(amount=Decimal("999000.00"), account=wallet, category=groceries)
        deleted.soft_delete()

        balances = account_balances(self.owner)
        self.assertEqual(balances[wallet.id], Decimal("800000.00"))
        self.assertEqual(balances[bank.id], Decimal("4700000.00"))
        self.assertEqual(balances[card.id], Decimal("0.00"))

        start, end = month_range(date(2026, 9, 1))
        totals = month_totals(self.owner, start, end)
        self.assertEqual(totals["income"], Decimal("5000000.00"))
        self.assertEqual(totals["expense"], Decimal("500000.00"))
        self.assertEqual(totals["net"], Decimal("4500000.00"))

    def test_balances_and_totals_never_mix_two_users(self) -> None:
        mine = self.make_account(opening_balance=Decimal("100000.00"))
        theirs = self.make_account(self.other, opening_balance=Decimal("777000.00"))
        self.make_transaction(self.other, amount=Decimal("5000.00"), account=theirs)

        balances = account_balances(self.owner)
        self.assertEqual(list(balances), [mine.id])

        start, end = month_range(date(2026, 9, 1))
        self.assertEqual(month_totals(self.owner, start, end)["expense"], Decimal("0.00"))

    def test_default_categories_are_seeded_once(self) -> None:
        ensure_default_categories(self.owner)
        seeded = Category.objects.filter(user=self.owner).count()
        self.assertGreater(seeded, 0)

        Category.objects.filter(user=self.owner, type=Category.Type.INCOME).delete()
        ensure_default_categories(self.owner)
        self.assertLess(Category.objects.filter(user=self.owner).count(), seeded)


class MonthHelperTests(TestCase):
    def test_month_helpers_cover_their_edges(self) -> None:
        self.assertEqual(month_range(date(2026, 2, 17)), (date(2026, 2, 1), date(2026, 2, 28)))
        self.assertEqual(month_range(date(2024, 2, 17)), (date(2024, 2, 1), date(2024, 2, 29)))
        self.assertEqual(
            months_between(date(2026, 11, 15), date(2027, 1, 2)),
            [date(2026, 11, 1), date(2026, 12, 1), date(2027, 1, 1)],
        )
