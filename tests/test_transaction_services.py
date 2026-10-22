from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase

from prospectus_lumos.apps.accounts.models import DocumentSource
from prospectus_lumos.apps.documents.models import Document
from prospectus_lumos.apps.documents.services import resolve_monthly_document
from prospectus_lumos.apps.transactions.models import Transaction
from prospectus_lumos.apps.transactions.services import (
    ImportedTransactionEditError,
    create_manual_transaction,
    edit_manual_transaction,
)


class ManualTransactionCreationTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(username="manual-owner", password="pass")

    def _create(self, **overrides: object) -> Transaction:
        values = {
            "user": self.user,
            "transaction_type": Transaction.TransactionType.EXPENSE,
            "date": date(2026, 9, 30),
            "amount": Decimal("125000.50"),
            "description": "Weekly groceries",
            "category": "Food",
        }
        values.update(overrides)
        return create_manual_transaction(**values)  # type: ignore[arg-type]

    def test_creates_in_resolved_month_and_updates_combined_summary(self) -> None:
        document = resolve_monthly_document(user=self.user, year=2026, month=9)
        Transaction.objects.create(
            document=document,
            transaction_type=Transaction.TransactionType.INCOME,
            date="1/9/2026",
            amount=Decimal("500000.00"),
            description="Imported salary",
            category="Income",
            origin=Transaction.Origin.GOOGLE_SHEETS,
        )

        created = self._create()

        document.refresh_from_db()
        self.assertEqual(created.user, self.user)
        self.assertEqual(created.document, document)
        self.assertEqual(created.origin, Transaction.Origin.MANUAL)
        self.assertEqual(created.date, "2026-09-30")
        self.assertEqual(created.description, "Weekly groceries")
        self.assertEqual(created.category, "Food")
        self.assertEqual(document.total_income, Decimal("500000.00"))
        self.assertEqual(document.total_expenses, Decimal("125000.50"))
        self.assertEqual(document.income_count, 1)
        self.assertEqual(document.expenses_count, 1)

    def test_creates_separate_owner_scoped_documents(self) -> None:
        other = User.objects.create_user(username="other-manual-owner", password="pass")

        own = self._create()
        other_entry = self._create(user=other, transaction_type=Transaction.TransactionType.INCOME)

        self.assertNotEqual(own.document_id, other_entry.document_id)
        self.assertEqual(Document.objects.filter(user=self.user).count(), 1)
        self.assertEqual(Document.objects.filter(user=other).count(), 1)
        self.assertEqual(DocumentSource.objects.filter(user=self.user).count(), 1)
        self.assertEqual(DocumentSource.objects.filter(user=other).count(), 1)

    def test_rolls_back_the_transaction_and_new_month_if_summary_update_fails(self) -> None:
        with (
            patch(
                "prospectus_lumos.apps.transactions.services.recalculate_document_summary",
                side_effect=RuntimeError("summary failed"),
            ),
            self.assertRaisesMessage(RuntimeError, "summary failed"),
        ):
            self._create()

        self.assertFalse(Transaction.objects.exists())
        self.assertFalse(Document.objects.exists())
        self.assertFalse(DocumentSource.objects.exists())


class ManualTransactionEditTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(username="edit-owner", password="pass")
        self.other_user = User.objects.create_user(username="edit-other", password="pass")
        self.september = resolve_monthly_document(user=self.user, year=2026, month=9)
        self.manual = Transaction.objects.create(
            user=self.user,
            document=self.september,
            transaction_type=Transaction.TransactionType.EXPENSE,
            date="2026-09-30",
            amount=Decimal("125000.50"),
            description="Weekly groceries",
            category="Food",
            origin=Transaction.Origin.MANUAL,
        )
        self.imported = Transaction.objects.create(
            document=self.september,
            transaction_type=Transaction.TransactionType.INCOME,
            date="1/9/2026",
            amount=Decimal("500000.00"),
            description="Imported salary",
            category="Income",
            origin=Transaction.Origin.GOOGLE_SHEETS,
        )

    def _edit(self, **overrides: object) -> Transaction:
        values = {
            "user": self.user,
            "transaction_id": self.manual.pk,
            "transaction_type": Transaction.TransactionType.INCOME,
            "date": date(2026, 9, 29),
            "amount": Decimal("225000.75"),
            "description": "Freelance work",
            "category": "Side income",
        }
        values.update(overrides)
        return edit_manual_transaction(**values)  # type: ignore[arg-type]

    def test_edits_manual_row_in_place_and_updates_combined_summary(self) -> None:
        edited = self._edit()

        self.september.refresh_from_db()
        self.assertEqual(edited.pk, self.manual.pk)
        self.assertEqual(edited.user, self.user)
        self.assertEqual(edited.document, self.september)
        self.assertEqual(edited.origin, Transaction.Origin.MANUAL)
        self.assertEqual(edited.transaction_type, Transaction.TransactionType.INCOME)
        self.assertEqual(edited.date, "2026-09-29")
        self.assertEqual(edited.amount, Decimal("225000.75"))
        self.assertEqual(edited.description, "Freelance work")
        self.assertEqual(edited.category, "Side income")
        self.assertEqual(self.september.total_income, Decimal("725000.75"))
        self.assertEqual(self.september.total_expenses, Decimal("0.00"))
        self.assertEqual(self.september.income_count, 2)
        self.assertEqual(self.september.expenses_count, 0)
        self.assertTrue(Transaction.objects.filter(pk=self.imported.pk).exists())

    def test_moves_between_months_and_updates_both_summaries(self) -> None:
        october = resolve_monthly_document(user=self.user, year=2026, month=10)
        october_import = Transaction.objects.create(
            document=october,
            transaction_type=Transaction.TransactionType.EXPENSE,
            date="02/10/2026",
            amount=Decimal("10000.00"),
            description="Imported fee",
            category="Fees",
            origin=Transaction.Origin.DIRECT_UPLOAD,
        )

        edited = self._edit(
            transaction_type=Transaction.TransactionType.EXPENSE,
            date=date(2026, 10, 1),
            amount=Decimal("25000.00"),
        )

        self.september.refresh_from_db()
        october.refresh_from_db()
        self.assertEqual(edited.pk, self.manual.pk)
        self.assertEqual(edited.document, october)
        self.assertEqual(self.september.total_income, Decimal("500000.00"))
        self.assertEqual(self.september.total_expenses, Decimal("0.00"))
        self.assertEqual(self.september.income_count, 1)
        self.assertEqual(self.september.expenses_count, 0)
        self.assertEqual(october.total_income, Decimal("0.00"))
        self.assertEqual(october.total_expenses, Decimal("35000.00"))
        self.assertEqual(october.income_count, 0)
        self.assertEqual(october.expenses_count, 2)
        self.assertTrue(Transaction.objects.filter(pk=self.imported.pk, document=self.september).exists())
        self.assertTrue(Transaction.objects.filter(pk=october_import.pk, document=october).exists())

    def test_rejects_imported_rows_and_hides_other_users_rows(self) -> None:
        imported_values = {
            "user": self.user,
            "transaction_id": self.imported.pk,
            "transaction_type": Transaction.TransactionType.EXPENSE,
            "date": date(2026, 10, 1),
            "amount": Decimal("1.00"),
            "description": "Changed",
            "category": "Changed",
        }

        with self.assertRaisesMessage(
            ImportedTransactionEditError,
            "Imported transactions must be edited through their source workflow.",
        ):
            edit_manual_transaction(**imported_values)  # type: ignore[arg-type]
        with self.assertRaises(Transaction.DoesNotExist):
            self._edit(user=self.other_user)

        self.imported.refresh_from_db()
        self.manual.refresh_from_db()
        self.assertEqual(self.imported.origin, Transaction.Origin.GOOGLE_SHEETS)
        self.assertEqual(self.imported.description, "Imported salary")
        self.assertEqual(self.manual.description, "Weekly groceries")
        self.assertFalse(Document.objects.filter(user=self.user, year=2026, month=10).exists())

    def test_rolls_back_move_and_new_month_when_summary_update_fails(self) -> None:
        with (
            patch(
                "prospectus_lumos.apps.transactions.services.recalculate_document_summary",
                side_effect=RuntimeError("summary failed"),
            ),
            self.assertRaisesMessage(RuntimeError, "summary failed"),
        ):
            self._edit(date=date(2026, 10, 1))

        self.manual.refresh_from_db()
        self.assertEqual(self.manual.document, self.september)
        self.assertEqual(self.manual.date, "2026-09-30")
        self.assertEqual(self.manual.amount, Decimal("125000.50"))
        self.assertFalse(Document.objects.filter(user=self.user, year=2026, month=10).exists())
