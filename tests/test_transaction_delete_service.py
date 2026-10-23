from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase

from prospectus_lumos.apps.documents.models import Document
from prospectus_lumos.apps.documents.services import lock_documents, recalculate_document_summary
from prospectus_lumos.apps.transactions.models import Transaction
from prospectus_lumos.apps.transactions.services import (
    ImportedTransactionDeleteError,
    TransactionDeleteConflict,
    create_manual_transaction,
    delete_manual_transaction,
)


class ManualTransactionDeletionTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(username="delete-owner")
        self.other_user = User.objects.create_user(username="delete-other")
        self.manual = create_manual_transaction(
            user=self.user,
            transaction_type=Transaction.TransactionType.EXPENSE,
            date=date(2026, 9, 30),
            amount=Decimal("125000.50"),
            description="Groceries",
            category="Food",
        )
        self.document = self.manual.document
        self.document.google_sheet_id = "retained-sheet"
        self.document.google_sheet_name = "Monthly budget September 2026"
        self.document.csv_file = "documents/csv/retained.csv"
        self.document.save(update_fields=["google_sheet_id", "google_sheet_name", "csv_file"])
        self.imported = Transaction.objects.create(
            document=self.document,
            transaction_type=Transaction.TransactionType.INCOME,
            date="1/9/2026",
            amount=Decimal("500000.00"),
            description="Imported salary",
            origin=Transaction.Origin.GOOGLE_SHEETS,
        )
        recalculate_document_summary(document=self.document)

    def test_deletes_manual_rows_and_preserves_imported_data_and_document(self) -> None:
        income = create_manual_transaction(
            user=self.user,
            transaction_type=Transaction.TransactionType.INCOME,
            date=date(2026, 9, 29),
            amount=Decimal("25000.75"),
            description="Side income",
            category="Income",
        )
        retained = list(Transaction.objects.exclude(pk=self.manual.pk).values())
        metadata_fields = ("id", "source_id", "google_sheet_id", "google_sheet_name", "csv_file", "processed_at")
        document_data = Document.objects.values(*metadata_fields).get(pk=self.document.pk)

        delete_manual_transaction(user=self.user, transaction_id=self.manual.pk)

        self.assertFalse(Transaction.objects.filter(pk=self.manual.pk).exists())
        self.assertEqual(list(Transaction.objects.values()), retained)
        self.document.refresh_from_db()
        self.assertEqual(self.document.total_income, Decimal("525000.75"))
        self.assertEqual(self.document.total_expenses, Decimal("0.00"))
        self.assertEqual((self.document.income_count, self.document.expenses_count), (2, 0))
        self.assertEqual(Document.objects.values(*metadata_fields).get(pk=self.document.pk), document_data)

        delete_manual_transaction(user=self.user, transaction_id=income.pk)

        self.document.refresh_from_db()
        self.assertEqual(self.document.total_income, Decimal("500000.00"))
        self.assertEqual((self.document.income_count, self.document.expenses_count), (1, 0))
        self.assertTrue(Transaction.objects.filter(pk=self.imported.pk).exists())

    def test_last_manual_row_leaves_an_empty_document_with_zero_summary(self) -> None:
        self.imported.delete()
        delete_manual_transaction(user=self.user, transaction_id=self.manual.pk)

        self.document.refresh_from_db()
        self.assertEqual(self.document.total_income, Decimal("0.00"))
        self.assertEqual(self.document.total_expenses, Decimal("0.00"))
        self.assertEqual((self.document.income_count, self.document.expenses_count), (0, 0))

    def test_refuses_both_import_origins_and_hides_foreign_or_missing_rows(self) -> None:
        with self.assertRaises(Transaction.DoesNotExist):
            delete_manual_transaction(user=self.other_user, transaction_id=self.manual.pk)
        with self.assertRaises(Transaction.DoesNotExist):
            delete_manual_transaction(user=self.other_user, transaction_id=self.imported.pk)
        with self.assertRaises(Transaction.DoesNotExist):
            delete_manual_transaction(user=self.user, transaction_id=-1)
        for origin in (Transaction.Origin.GOOGLE_SHEETS, Transaction.Origin.DIRECT_UPLOAD):
            with self.subTest(origin=origin):
                self.imported.origin = origin
                self.imported.save(update_fields=["origin"])
                with self.assertRaisesMessage(ImportedTransactionDeleteError, "through their source workflow"):
                    delete_manual_transaction(user=self.user, transaction_id=self.imported.pk)

        self.assertEqual(Transaction.objects.count(), 2)
        self.document.refresh_from_db()
        self.assertEqual(self.document.total_expenses, Decimal("125000.50"))
        self.assertEqual(self.document.total_income, Decimal("500000.00"))

    def test_supports_document_ownership_and_owner_only_rows(self) -> None:
        self.manual.user = None
        self.manual.save(update_fields=["user"])
        delete_manual_transaction(user=self.user, transaction_id=self.manual.pk)
        self.assertFalse(Transaction.objects.filter(pk=self.manual.pk).exists())

        standalone = Transaction.objects.create(
            user=self.user, transaction_type="expense", date="2026-09-30", amount=1, description="Standalone"
        )
        with self.assertRaises(Transaction.DoesNotExist):
            delete_manual_transaction(user=self.other_user, transaction_id=standalone.pk)
        delete_manual_transaction(user=self.user, transaction_id=standalone.pk)
        self.assertFalse(Transaction.objects.filter(pk=standalone.pk).exists())

    def test_rolls_back_deletion_and_written_summary_on_failure(self) -> None:
        def fail_after_summary_write(*, document: Document) -> None:
            recalculate_document_summary(document=document)
            raise RuntimeError("summary failed")

        with (
            patch(
                "prospectus_lumos.apps.transactions.services.recalculate_document_summary",
                side_effect=fail_after_summary_write,
            ),
            self.assertRaisesMessage(RuntimeError, "summary failed"),
        ):
            delete_manual_transaction(user=self.user, transaction_id=self.manual.pk)

        self.manual.refresh_from_db()
        self.document.refresh_from_db()
        self.assertEqual(self.manual.amount, Decimal("125000.50"))
        self.assertEqual(self.document.total_expenses, Decimal("125000.50"))
        self.assertEqual((self.document.income_count, self.document.expenses_count), (1, 1))

    def test_rechecks_month_and_origin_after_acquiring_locks(self) -> None:
        for changes, error in (
            ({"document_id": None}, TransactionDeleteConflict),
            ({"origin": Transaction.Origin.GOOGLE_SHEETS}, ImportedTransactionDeleteError),
        ):
            with self.subTest(changes=changes):

                def change_while_locking(
                    *documents: Document, changes: Mapping[str, object] = changes
                ) -> list[Document]:
                    Transaction.objects.filter(pk=self.manual.pk).update(**changes)
                    return lock_documents(*documents)

                with (
                    patch(
                        "prospectus_lumos.apps.transactions.services.lock_documents", side_effect=change_while_locking
                    ),
                    self.assertRaises(error),
                ):
                    delete_manual_transaction(user=self.user, transaction_id=self.manual.pk)

                self.manual.refresh_from_db()
                self.assertEqual(self.manual.document_id, self.document.pk)
                self.assertEqual(self.manual.origin, Transaction.Origin.MANUAL)
