import threading
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection, transaction
from django.test import TestCase, TransactionTestCase

from prospectus_lumos.apps.accounts.models import DocumentSource
from prospectus_lumos.apps.documents.models import Document
from prospectus_lumos.apps.documents.services import (
    MANUAL_SOURCE_NAME,
    lock_documents,
    recalculate_document_summary,
    resolve_monthly_document,
)
from prospectus_lumos.apps.transactions.models import Transaction


class MonthlyDocumentResolverTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(username="document-owner", password="pass")

    def test_creates_and_reuses_a_manual_only_month(self) -> None:
        document = resolve_monthly_document(user=self.user, year=2026, month=9)
        resolved_again = resolve_monthly_document(user=self.user, year=2026, month=9)

        self.assertEqual(resolved_again.pk, document.pk)
        self.assertEqual(document.source.user, self.user)
        self.assertEqual(document.source.name, MANUAL_SOURCE_NAME)
        self.assertEqual(document.source.source_type, DocumentSource.SourceType.MANUAL)
        self.assertFalse(document.csv_file)
        self.assertEqual(Document.objects.count(), 1)
        self.assertEqual(DocumentSource.objects.count(), 1)

    def test_keeps_existing_import_document_and_metadata(self) -> None:
        imported_source = DocumentSource.objects.create(
            user=self.user,
            source_type=DocumentSource.SourceType.DIRECT_UPLOAD,
            name="Imported budget",
        )
        imported_document = Document.objects.create(
            user=self.user,
            source=imported_source,
            year=2026,
            month=8,
            google_sheet_id="existing-sheet",
            google_sheet_name="August budget",
            csv_file=SimpleUploadedFile("august.csv", b"date,amount\n"),
        )

        resolved = resolve_monthly_document(user=self.user, year=2026, month=8)

        self.assertEqual(resolved.pk, imported_document.pk)
        self.assertEqual(resolved.source, imported_source)
        self.assertEqual(resolved.google_sheet_id, "existing-sheet")
        self.assertEqual(resolved.google_sheet_name, "August budget")
        self.assertEqual(resolved.csv_file.name, imported_document.csv_file.name)
        self.assertFalse(DocumentSource.objects.filter(source_type=DocumentSource.SourceType.MANUAL).exists())

    def test_reuses_an_existing_manual_source(self) -> None:
        manual_source = DocumentSource.objects.create(
            user=self.user,
            source_type=DocumentSource.SourceType.MANUAL,
            name="My manual source",
        )

        document = resolve_monthly_document(user=self.user, year=2026, month=9)

        self.assertEqual(document.source, manual_source)
        self.assertEqual(DocumentSource.objects.count(), 1)

    def test_monthly_documents_and_sources_are_user_scoped(self) -> None:
        other_user = User.objects.create_user(username="other-document-owner", password="pass")

        own_document = resolve_monthly_document(user=self.user, year=2026, month=9)
        other_document = resolve_monthly_document(user=other_user, year=2026, month=9)

        self.assertNotEqual(own_document.pk, other_document.pk)
        self.assertNotEqual(own_document.source_id, other_document.source_id)

    def test_rejects_an_invalid_month(self) -> None:
        with self.assertRaisesMessage(ValueError, "month must be between 1 and 12"):
            resolve_monthly_document(user=self.user, year=2026, month=13)

        self.assertFalse(Document.objects.exists())
        self.assertFalse(DocumentSource.objects.exists())

    def test_does_not_reuse_a_non_manual_reserved_source(self) -> None:
        DocumentSource.objects.create(
            user=self.user,
            source_type=DocumentSource.SourceType.DIRECT_UPLOAD,
            name=MANUAL_SOURCE_NAME,
        )

        with self.assertRaisesMessage(ValueError, "reserved document source name"):
            resolve_monthly_document(user=self.user, year=2026, month=9)

        self.assertFalse(Document.objects.exists())

    def test_records_the_given_source_for_a_new_month(self) -> None:
        imported_source = DocumentSource.objects.create(
            user=self.user,
            source_type=DocumentSource.SourceType.GOOGLE_DRIVE,
            name="Drive budgets",
        )

        document = resolve_monthly_document(user=self.user, year=2026, month=7, source=imported_source)

        self.assertEqual(document.source, imported_source)
        self.assertFalse(DocumentSource.objects.filter(source_type=DocumentSource.SourceType.MANUAL).exists())

    def test_returns_the_document_a_racing_writer_created(self) -> None:
        manual_source = DocumentSource.objects.create(
            user=self.user,
            source_type=DocumentSource.SourceType.MANUAL,
            name="My manual source",
        )
        competing_document: list[Document] = []

        def create_competing_document(*, user: User) -> DocumentSource:
            """Stand in for another writer winning the insert between the lookup and the create."""

            competing_document.append(Document.objects.create(user=user, source=manual_source, year=2026, month=9))
            return manual_source

        with patch(
            "prospectus_lumos.apps.documents.services._resolve_manual_source",
            side_effect=create_competing_document,
        ):
            resolved = resolve_monthly_document(user=self.user, year=2026, month=9)

        self.assertEqual(resolved.pk, competing_document[0].pk)
        self.assertEqual(Document.objects.filter(user=self.user, year=2026, month=9).count(), 1)


class DocumentSummaryTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(username="summary-owner", password="pass")
        self.document = resolve_monthly_document(user=self.user, year=2026, month=9)

    def _add_transaction(
        self,
        *,
        transaction_type: str,
        amount: str,
        document: Document | None = None,
    ) -> Transaction:
        return Transaction.objects.create(
            document=self.document if document is None else document,
            transaction_type=transaction_type,
            origin=Transaction.Origin.MANUAL,
            date="2026-09-01",
            amount=Decimal(amount),
            description="row",
        )

    def test_totals_and_counts_come_from_the_current_rows(self) -> None:
        self._add_transaction(transaction_type=Transaction.TransactionType.INCOME, amount="500000.00")
        self._add_transaction(transaction_type=Transaction.TransactionType.INCOME, amount="250000.50")
        self._add_transaction(transaction_type=Transaction.TransactionType.EXPENSE, amount="15000.25")

        returned = recalculate_document_summary(document=self.document)

        self.document.refresh_from_db()
        self.assertIs(returned, self.document)
        for document in (returned, self.document):
            self.assertEqual(document.total_income, Decimal("750000.50"))
            self.assertEqual(document.total_expenses, Decimal("15000.25"))
            self.assertEqual(document.income_count, 2)
            self.assertEqual(document.expenses_count, 1)

    def test_removed_rows_reset_the_summary_to_zero(self) -> None:
        self._add_transaction(transaction_type=Transaction.TransactionType.EXPENSE, amount="15000.00")
        recalculate_document_summary(document=self.document)

        self.document.transactions.all().delete()
        recalculate_document_summary(document=self.document)

        self.document.refresh_from_db()
        self.assertEqual(self.document.total_income, Decimal("0"))
        self.assertEqual(self.document.total_expenses, Decimal("0"))
        self.assertEqual(self.document.income_count, 0)
        self.assertEqual(self.document.expenses_count, 0)

    def test_other_months_and_unfiled_rows_are_excluded(self) -> None:
        other_month = resolve_monthly_document(user=self.user, year=2026, month=10)
        self._add_transaction(
            transaction_type=Transaction.TransactionType.EXPENSE, amount="99000.00", document=other_month
        )
        Transaction.objects.create(
            user=self.user,
            transaction_type=Transaction.TransactionType.EXPENSE,
            origin=Transaction.Origin.MANUAL,
            date="2026-09-02",
            amount=Decimal("77000.00"),
            description="not filed under a month",
        )
        self._add_transaction(transaction_type=Transaction.TransactionType.EXPENSE, amount="15000.00")

        recalculate_document_summary(document=self.document)

        self.document.refresh_from_db()
        self.assertEqual(self.document.total_expenses, Decimal("15000.00"))
        self.assertEqual(self.document.expenses_count, 1)

    def test_an_unsaved_document_cannot_be_summarized(self) -> None:
        unsaved = Document(user=self.user, source=self.document.source, year=2026, month=11)

        with self.assertRaisesMessage(ValueError, "documents must be saved"):
            recalculate_document_summary(document=unsaved)

    def test_a_deleted_month_is_reported_rather_than_silently_written(self) -> None:
        stale = self.document
        Document.objects.filter(pk=stale.pk).delete()

        with self.assertRaises(Document.DoesNotExist):
            recalculate_document_summary(document=stale)


class DocumentLockingTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(username="locking-owner", password="pass")
        self.september = resolve_monthly_document(user=self.user, year=2026, month=9)
        self.october = resolve_monthly_document(user=self.user, year=2026, month=10)

    def test_months_are_locked_in_primary_key_order_whatever_the_argument_order(self) -> None:
        with transaction.atomic():
            locked = lock_documents(self.october, self.september)

        self.assertEqual([document.pk for document in locked], sorted([self.september.pk, self.october.pk]))

    def test_repeated_months_are_locked_once(self) -> None:
        with transaction.atomic():
            locked = lock_documents(self.september, self.september)

        self.assertEqual([document.pk for document in locked], [self.september.pk])

    def test_locking_nothing_is_allowed(self) -> None:
        with transaction.atomic():
            self.assertEqual(lock_documents(), [])


class ConcurrentDocumentTests(TransactionTestCase):
    """Exercise the real locking behavior, which needs committed rows and separate connections."""

    def setUp(self) -> None:
        self.user = User.objects.create_user(username="concurrent-owner", password="pass")

    def test_locking_requires_an_enclosing_transaction(self) -> None:
        document = resolve_monthly_document(user=self.user, year=2026, month=9)

        with self.assertRaises(transaction.TransactionManagementError):
            lock_documents(document)

        with self.assertRaises(transaction.TransactionManagementError):
            resolve_monthly_document(user=self.user, year=2026, month=9, for_update=True)

    def test_a_resolved_month_can_be_locked_inside_a_transaction(self) -> None:
        document = resolve_monthly_document(user=self.user, year=2026, month=9)

        with transaction.atomic():
            locked = resolve_monthly_document(user=self.user, year=2026, month=9, for_update=True)

        self.assertEqual(locked.pk, document.pk)

    def test_concurrent_resolution_keeps_one_document_per_month(self) -> None:
        start = threading.Barrier(4)
        resolved_ids: list[int] = []
        failures: list[BaseException] = []
        lock = threading.Lock()

        def resolve() -> None:
            try:
                start.wait(timeout=10)
                document = resolve_monthly_document(user=self.user, year=2026, month=9)
                with lock:
                    resolved_ids.append(document.pk)
            except BaseException as error:  # noqa: B036 - surfaced through the assertions below
                with lock:
                    failures.append(error)
            finally:
                connection.close()

        threads = [threading.Thread(target=resolve) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertEqual(failures, [])
        self.assertEqual(len(resolved_ids), 4)
        self.assertEqual(len(set(resolved_ids)), 1)
        self.assertEqual(Document.objects.filter(user=self.user, year=2026, month=9).count(), 1)

    def test_a_held_month_lock_blocks_the_next_writer(self) -> None:
        september = resolve_monthly_document(user=self.user, year=2026, month=9)
        october = resolve_monthly_document(user=self.user, year=2026, month=10)
        events: list[str] = []
        holder_locked = threading.Event()
        waiter_attempting = threading.Event()
        holder_may_commit = threading.Event()

        def hold_both_months() -> None:
            try:
                with transaction.atomic():
                    lock_documents(october, september)
                    events.append("holder-locked")
                    holder_locked.set()
                    holder_may_commit.wait(timeout=10)
                events.append("holder-committed")
            finally:
                connection.close()

        def wait_for_september() -> None:
            try:
                holder_locked.wait(timeout=10)
                waiter_attempting.set()
                with transaction.atomic():
                    lock_documents(september)
                    events.append("waiter-locked")
            finally:
                connection.close()

        holder = threading.Thread(target=hold_both_months)
        waiter = threading.Thread(target=wait_for_september)
        holder.start()
        waiter.start()

        self.assertTrue(waiter_attempting.wait(timeout=10))
        # The waiter cannot take September while the holder's transaction is open.
        threading.Event().wait(0.5)
        self.assertNotIn("waiter-locked", events)

        holder_may_commit.set()
        holder.join(timeout=10)
        waiter.join(timeout=10)

        self.assertEqual(events, ["holder-locked", "holder-committed", "waiter-locked"])

    def test_opposing_cross_month_lock_orders_do_not_deadlock(self) -> None:
        september = resolve_monthly_document(user=self.user, year=2026, month=9)
        october = resolve_monthly_document(user=self.user, year=2026, month=10)
        start = threading.Barrier(2)
        failures: list[BaseException] = []
        completed: list[str] = []
        lock = threading.Lock()

        def move_between_months(name: str, first: Document, second: Document) -> None:
            try:
                for _ in range(10):
                    start.wait(timeout=10)
                    with transaction.atomic():
                        lock_documents(first, second)
                        recalculate_document_summary(document=first)
                        recalculate_document_summary(document=second)
                with lock:
                    completed.append(name)
            except BaseException as error:  # noqa: B036 - surfaced through the assertions below
                with lock:
                    failures.append(error)
                start.abort()
            finally:
                connection.close()

        threads = [
            threading.Thread(target=move_between_months, args=("forward", september, october)),
            threading.Thread(target=move_between_months, args=("backward", october, september)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        self.assertEqual(failures, [])
        self.assertEqual(sorted(completed), ["backward", "forward"])
