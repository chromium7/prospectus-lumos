from __future__ import annotations

import tempfile
from decimal import Decimal
from tempfile import TemporaryDirectory
from unittest.mock import patch, MagicMock

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse

from libraries.google_cloud.tuples import File
from prospectus_lumos.apps.accounts.models import GoogleDriveCredentials, DocumentSource
from prospectus_lumos.apps.documents.models import Document
from prospectus_lumos.apps.documents.services import recalculate_document_summary, resolve_monthly_document
from prospectus_lumos.apps.expenses.services import ExpenseAnalyzerService, ExpenseSheetService
from prospectus_lumos.apps.transactions.models import Transaction


class GoogleDriveSyncTestCase(TestCase):
    """Shared Google Drive source fixture for the import and re-sync services."""

    def setUp(self) -> None:
        self.tempdir = TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        # Ensure files save to temp dir in tests
        self._media_override = override_settings(MEDIA_ROOT=self.tempdir.name)
        self._media_override.enable()
        self.addCleanup(self._media_override.disable)

        self.user = User.objects.create_user(username="tester", password="pass")

        # Create credentials with a dummy json file
        self.credentials = GoogleDriveCredentials.objects.create(
            user=self.user,
            drive_folder_url="https://drive.google.com/drive/folders/ABCDE",
            folder_id="Budget",  # used as a path by our backend wrapper
            is_active=True,
        )
        self.credentials.service_account_file.save("service.json", ContentFile(b"{}"))

        self.source = DocumentSource.objects.create(
            user=self.user,
            source_type="google_drive",
            name="Main Drive",
            google_credentials=self.credentials,
            is_active=True,
        )


class ExpenseSheetServiceSyncTests(GoogleDriveSyncTestCase):
    @patch("prospectus_lumos.apps.expenses.services.GoogleDriveBackend")
    def test_sync_creates_document_csv_and_transactions(self, backend_cls: MagicMock) -> None:
        backend = MagicMock()
        backend_cls.return_value = backend

        # Simulate one sheet file
        backend.list_monthly_budget_files.return_value = [
            File(key="sheet123", name="Monthly Budget Aug 2025", extension="gsheet", size=0)
        ]

        # Parsed results from sheet
        expenses = [
            {"date": "1/8/2025", "amount": 15000, "description": "Snacks", "category": "Food", "type": "expenses"}
        ]
        income = [
            {"date": "2/8/2025", "amount": 500000, "description": "Paycheck", "category": "Paycheck", "type": "income"}
        ]
        backend.parse_monthly_budget_sheet.return_value = (expenses, income)

        service = ExpenseSheetService(self.user)
        docs = service.sync_google_drive_documents(self.source)

        self.assertEqual(len(docs), 1)
        doc = docs[0]

        # Document fields
        self.assertEqual(doc.month, 8)
        self.assertEqual(doc.year, 2025)
        self.assertEqual(doc.google_sheet_id, "sheet123")
        self.assertEqual(doc.total_income, 500000)
        self.assertEqual(doc.total_expenses, 15000)
        self.assertEqual(doc.income_count, 1)
        self.assertEqual(doc.expenses_count, 1)

        # CSV saved
        self.assertTrue(doc.csv_file)
        csv_text = doc.csv_file.read().decode("utf-8")
        expected_lines = [
            "name,amount,description,category,expense/income",
            "Snacks,15000,Snacks,Food,expense",
            "Paycheck,500000,Paycheck,Paycheck,income",
        ]
        for line in expected_lines:
            self.assertIn(line, csv_text)

        # Transactions created
        txs = Transaction.objects.filter(document=doc).order_by("transaction_type")
        self.assertEqual({tx.origin for tx in txs}, {Transaction.Origin.GOOGLE_SHEETS})
        self.assertEqual(txs.count(), 2)
        self.assertEqual({t.transaction_type for t in txs}, {"expense", "income"})

    @patch("prospectus_lumos.apps.expenses.services.GoogleDriveBackend")
    def test_sync_adopts_a_manual_month_and_combines_its_summary(self, backend_cls: MagicMock) -> None:
        manual_document = resolve_monthly_document(user=self.user, year=2025, month=8)
        manual_row = Transaction.objects.create(
            document=manual_document,
            transaction_type=Transaction.TransactionType.EXPENSE,
            origin=Transaction.Origin.MANUAL,
            date="3/8/2025",
            amount=Decimal("2000"),
            description="Manual coffee",
        )

        backend = MagicMock()
        backend_cls.return_value = backend
        backend.list_monthly_budget_files.return_value = [
            File(key="sheet123", name="Monthly Budget Aug 2025", extension="gsheet", size=0)
        ]
        backend.parse_monthly_budget_sheet.return_value = (
            [{"date": "1/8/2025", "amount": 15000, "description": "Snacks", "category": "Food"}],
            [{"date": "2/8/2025", "amount": 500000, "description": "Paycheck", "category": "Paycheck"}],
        )

        docs = ExpenseSheetService(self.user).sync_google_drive_documents(self.source)

        self.assertEqual([doc.pk for doc in docs], [manual_document.pk])
        self.assertEqual(Document.objects.filter(user=self.user, year=2025, month=8).count(), 1)
        self.assertTrue(Transaction.objects.filter(pk=manual_row.pk).exists())

        manual_document.refresh_from_db()
        self.assertEqual(manual_document.total_expenses, Decimal("17000"))
        self.assertEqual(manual_document.total_income, Decimal("500000"))
        self.assertEqual(manual_document.expenses_count, 2)
        self.assertEqual(manual_document.income_count, 1)

    @patch("prospectus_lumos.apps.expenses.services.GoogleDriveBackend")
    def test_sync_attaches_the_importing_source_to_an_adopted_manual_month(self, backend_cls: MagicMock) -> None:
        manual_document = resolve_monthly_document(user=self.user, year=2025, month=8)
        manual_source = manual_document.source
        self.assertEqual(manual_source.source_type, DocumentSource.SourceType.MANUAL)
        manual_row = Transaction.objects.create(
            document=manual_document,
            transaction_type=Transaction.TransactionType.EXPENSE,
            origin=Transaction.Origin.MANUAL,
            date="3/8/2025",
            amount=Decimal("2000"),
            description="Manual coffee",
        )

        backend = MagicMock()
        backend_cls.return_value = backend
        backend.list_monthly_budget_files.return_value = [
            File(key="sheet123", name="Monthly Budget Aug 2025", extension="gsheet", size=0)
        ]
        backend.parse_monthly_budget_sheet.return_value = (
            [{"date": "1/8/2025", "amount": 15000, "description": "Snacks", "category": "Food"}],
            [],
        )

        ExpenseSheetService(self.user).sync_google_drive_documents(self.source)

        manual_document.refresh_from_db()
        self.assertEqual(manual_document.source_id, self.source.pk)
        self.assertEqual(manual_document.google_sheet_id, "sheet123")
        self.assertEqual(manual_document.google_sheet_name, "Monthly Budget Aug 2025")

        manual_row.refresh_from_db()
        self.assertEqual(manual_row.origin, Transaction.Origin.MANUAL)
        self.assertEqual(
            sorted(manual_document.transactions.values_list("origin", flat=True)),
            [Transaction.Origin.GOOGLE_SHEETS, Transaction.Origin.MANUAL],
        )

    @patch("prospectus_lumos.apps.expenses.services.GoogleDriveBackend")
    def test_sync_keeps_rows_imported_by_another_path(self, backend_cls: MagicMock) -> None:
        upload_source = DocumentSource.objects.create(
            user=self.user,
            source_type=DocumentSource.SourceType.DIRECT_UPLOAD,
            name="Statement upload",
        )
        document = resolve_monthly_document(user=self.user, year=2025, month=8, source=upload_source)
        uploaded_row = Transaction.objects.create(
            document=document,
            transaction_type=Transaction.TransactionType.EXPENSE,
            origin=Transaction.Origin.DIRECT_UPLOAD,
            date="4/8/2025",
            amount=Decimal("5000"),
            description="Uploaded statement row",
        )

        backend = MagicMock()
        backend_cls.return_value = backend
        backend.list_monthly_budget_files.return_value = [
            File(key="sheet123", name="Monthly Budget Aug 2025", extension="gsheet", size=0)
        ]
        backend.parse_monthly_budget_sheet.return_value = (
            [{"date": "1/8/2025", "amount": 15000, "description": "Snacks", "category": "Food"}],
            [],
        )

        ExpenseSheetService(self.user).sync_google_drive_documents(self.source)

        self.assertTrue(Transaction.objects.filter(pk=uploaded_row.pk).exists())
        document.refresh_from_db()
        self.assertEqual(document.total_expenses, Decimal("20000"))
        self.assertEqual(document.expenses_count, 2)

    @patch("prospectus_lumos.apps.expenses.services.GoogleDriveBackend")
    def test_sync_skips_existing_same_sheet_id(self, backend_cls: MagicMock) -> None:
        backend = MagicMock()
        backend_cls.return_value = backend
        backend.list_monthly_budget_files.return_value = [
            File(key="SAME", name="Monthly Budget Aug 2025", extension="gsheet", size=0)
        ]
        backend.parse_monthly_budget_sheet.return_value = ([], [])

        # Create existing document with same id
        existing = Document.objects.create(
            user=self.user, source=self.source, month=8, year=2025, google_sheet_id="SAME"
        )

        service = ExpenseSheetService(self.user)
        docs = service.sync_google_drive_documents(self.source)

        self.assertEqual(docs, [])
        existing.refresh_from_db()
        self.assertEqual(existing.google_sheet_id, "SAME")


class ExpenseSheetServiceResyncTests(GoogleDriveSyncTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.document = resolve_monthly_document(user=self.user, year=2025, month=8, source=self.source)
        self.document.google_sheet_id = "sheet123"
        self.document.google_sheet_name = "Monthly Budget Aug 2025"
        self.document.save(update_fields=["google_sheet_id", "google_sheet_name"])
        self.imported_row = Transaction.objects.create(
            document=self.document,
            transaction_type=Transaction.TransactionType.EXPENSE,
            origin=Transaction.Origin.GOOGLE_SHEETS,
            date="1/8/2025",
            amount=Decimal("15000"),
            description="Snacks",
            category="Food",
        )
        self.manual_row = Transaction.objects.create(
            document=self.document,
            transaction_type=Transaction.TransactionType.EXPENSE,
            origin=Transaction.Origin.MANUAL,
            date="3/8/2025",
            amount=Decimal("2000"),
            description="Manual coffee",
        )
        recalculate_document_summary(document=self.document)

    def _stub_backend(self, backend_cls: MagicMock) -> MagicMock:
        backend = MagicMock()
        backend_cls.return_value = backend
        backend.parse_monthly_budget_sheet.return_value = (
            [{"date": "1/8/2025", "amount": 18000, "description": "Snacks", "category": "Food"}],
            [{"date": "2/8/2025", "amount": 500000, "description": "Paycheck", "category": "Paycheck"}],
        )
        return backend

    @patch("prospectus_lumos.apps.expenses.services.GoogleDriveBackend")
    def test_resync_preserves_manual_rows_and_recombines_totals(self, backend_cls: MagicMock) -> None:
        self._stub_backend(backend_cls)

        ExpenseSheetService(self.user).resync_document(self.document)

        self.assertTrue(Transaction.objects.filter(pk=self.manual_row.pk).exists())
        self.assertFalse(Transaction.objects.filter(pk=self.imported_row.pk).exists())

        self.document.refresh_from_db()
        self.assertEqual(self.document.total_expenses, Decimal("20000"))
        self.assertEqual(self.document.total_income, Decimal("500000"))
        self.assertEqual(self.document.expenses_count, 2)
        self.assertEqual(self.document.income_count, 1)

    @patch("prospectus_lumos.apps.expenses.services.GoogleDriveBackend")
    def test_repeated_resync_does_not_duplicate_imported_rows(self, backend_cls: MagicMock) -> None:
        self._stub_backend(backend_cls)
        service = ExpenseSheetService(self.user)

        service.resync_document(self.document)
        service.resync_document(self.document)

        rows = self.document.transactions.all()
        self.assertEqual(rows.filter(origin=Transaction.Origin.GOOGLE_SHEETS).count(), 2)
        self.assertEqual(rows.filter(origin=Transaction.Origin.MANUAL).count(), 1)

        self.document.refresh_from_db()
        self.assertEqual(self.document.total_expenses, Decimal("20000"))
        self.assertEqual(self.document.total_income, Decimal("500000"))

    @patch("prospectus_lumos.apps.expenses.services.GoogleDriveBackend")
    def test_a_failed_sheet_read_leaves_the_month_untouched(self, backend_cls: MagicMock) -> None:
        backend = MagicMock()
        backend_cls.return_value = backend
        backend.parse_monthly_budget_sheet.side_effect = RuntimeError("sheet unavailable")

        with self.assertRaises(RuntimeError):
            ExpenseSheetService(self.user).resync_document(self.document)

        self.assertEqual(
            sorted(self.document.transactions.values_list("pk", flat=True)),
            sorted([self.imported_row.pk, self.manual_row.pk]),
        )
        self.document.refresh_from_db()
        self.assertEqual(self.document.total_expenses, Decimal("17000"))
        self.assertEqual(self.document.expenses_count, 2)

    def test_resync_requires_a_google_sheet_id(self) -> None:
        self.document.google_sheet_id = ""
        self.document.save(update_fields=["google_sheet_id"])

        with self.assertRaises(ValueError):
            ExpenseSheetService(self.user).resync_document(self.document)

    def test_resync_rejects_a_month_that_is_not_google_backed(self) -> None:
        manual_document = resolve_monthly_document(user=self.user, year=2025, month=7)

        with self.assertRaises(ValueError):
            ExpenseSheetService(self.user).resync_document(manual_document)


class DocumentDetailViewTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(username="tester", password="pass")

        self.source = DocumentSource.objects.create(
            user=self.user,
            source_type="google_drive",
            name="Main Drive",
            is_active=True,
        )

        self.document = Document.objects.create(
            user=self.user,
            source=self.source,
            month=8,
            year=2025,
        )

        # Create a couple of transactions with out-of-order dates to verify ordering
        Transaction.objects.create(
            document=self.document,
            transaction_type="expense",
            date="2025-08-15",
            amount=10000,
            description="Groceries",
            category="Food",
        )
        Transaction.objects.create(
            document=self.document,
            transaction_type="income",
            date="2025-08-01",
            amount=500000,
            description="Salary",
            category="Income",
        )

    def test_document_detail_view_shows_transactions_sorted_by_date(self) -> None:
        self.client.login(username="tester", password="pass")
        url = reverse("document_detail", args=[self.document.id])
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "expenses/document_detail.html")

        transactions = list(response.context["transactions"])
        self.assertEqual(len(transactions), 2)
        # Ensure ordered by date then id (oldest first)
        self.assertEqual(transactions[0].date, "2025-08-01")
        self.assertEqual(transactions[1].date, "2025-08-15")


class SyncDocumentsViewTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(username="tester", password="pass")
        self.credentials = GoogleDriveCredentials.objects.create(user=self.user)
        self.credentials.service_account_file.save("service.json", ContentFile(b"{}"))
        self.source = DocumentSource.objects.create(
            user=self.user,
            source_type="google_drive",
            name="Main",
            google_credentials=self.credentials,
            is_active=True,
        )

    @patch("prospectus_lumos.apps.expenses.services.ExpenseSheetService.sync_google_drive_documents")
    def test_view_triggers_sync_and_redirects(self, sync_mock: MagicMock) -> None:
        sync_mock.return_value = []
        self.client.login(username="tester", password="pass")
        response = self.client.post(reverse("sync_documents"))
        self.assertEqual(response.status_code, 302)
        # Django's test response does not always expose 'url'; use 'headers' or 'wsgi_request'
        self.assertIn(reverse("dashboard"), response.headers.get("Location", ""))
        self.assertTrue(sync_mock.called)


class ExpenseAnalyzerServiceExcludeTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(username="tester", password="pass")
        self.source = DocumentSource.objects.create(user=self.user, source_type="google_drive", name="Main")

        self.doc = Document.objects.create(user=self.user, source=self.source, month=8, year=2025)

        Transaction.objects.create(
            document=self.doc, transaction_type="expense", amount=10000, description="Rice", category="Food"
        )
        Transaction.objects.create(
            document=self.doc, transaction_type="expense", amount=5000, description="Bus", category="Transport"
        )
        Transaction.objects.create(
            document=self.doc, transaction_type="expense", amount=2000, description="Candy", category="Food"
        )
        Transaction.objects.create(
            document=self.doc, transaction_type="income", amount=100000, description="Salary", category="Paycheck"
        )
        Transaction.objects.create(
            document=self.doc, transaction_type="income", amount=20000, description="Freelance", category="Side Hustle"
        )

    def test_expense_analysis_excludes_single_category(self) -> None:
        service = ExpenseAnalyzerService(self.user)
        analysis = service.get_expense_analysis(exclude_categories=["Transport"])
        cats = analysis["expenses_by_category"]
        self.assertNotIn("Transport", cats)
        self.assertIn("Food", cats)
        # Total should exclude Transport
        self.assertEqual(analysis["total_expenses"], 12000)  # 10000 + 2000

    def test_expense_analysis_excludes_multiple_categories(self) -> None:
        service = ExpenseAnalyzerService(self.user)
        analysis = service.get_expense_analysis(exclude_categories=["Food", "Transport"])
        self.assertEqual(analysis["expenses_by_category"], {})
        self.assertEqual(analysis["total_expenses"], 0)

    def test_income_analysis_excludes_category(self) -> None:
        service = ExpenseAnalyzerService(self.user)
        analysis = service.get_income_analysis(exclude_categories=["Side Hustle"])
        cats = analysis["income_by_category"]
        self.assertNotIn("Side Hustle", cats)
        self.assertIn("Paycheck", cats)
        self.assertEqual(analysis["total_income"], 100000)

    def test_analysis_unaffected_when_exclude_is_none(self) -> None:
        service = ExpenseAnalyzerService(self.user)
        analysis = service.get_expense_analysis(exclude_categories=None)
        cats = analysis["expenses_by_category"]
        self.assertIn("Food", cats)
        self.assertIn("Transport", cats)
        self.assertEqual(analysis["total_expenses"], 17000)

    def test_analysis_unaffected_when_exclude_is_empty(self) -> None:
        service = ExpenseAnalyzerService(self.user)
        analysis = service.get_expense_analysis(exclude_categories=[])
        cats = analysis["expenses_by_category"]
        self.assertIn("Food", cats)
        self.assertIn("Transport", cats)
        self.assertEqual(analysis["total_expenses"], 17000)


class AnalyzerViewExcludeTests(TestCase):
    def setUp(self) -> None:
        self._media_override = override_settings(MEDIA_ROOT=tempfile.mkdtemp())
        self._media_override.enable()
        self.addCleanup(self._media_override.disable)

        self.user = User.objects.create_user(username="tester", password="pass")
        self.source = DocumentSource.objects.create(user=self.user, source_type="google_drive", name="Main")

        self.doc = Document.objects.create(user=self.user, source=self.source, month=8, year=2025)

        Transaction.objects.create(
            document=self.doc, transaction_type="expense", amount=5000, description="Bus", category="Transport"
        )
        Transaction.objects.create(
            document=self.doc, transaction_type="expense", amount=10000, description="Rice", category="Food"
        )
        Transaction.objects.create(
            document=self.doc, transaction_type="income", amount=100000, description="Salary", category="Paycheck"
        )
        Transaction.objects.create(
            document=self.doc, transaction_type="income", amount=20000, description="Freelance", category="Side Hustle"
        )

    def test_expense_analyzer_view_includes_exclude_categories_context(self) -> None:
        self.client.login(username="tester", password="pass")
        url = reverse("expense_analyzer") + "?exclude_category=Transport"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        ctx = response.context
        self.assertIn("Transport", ctx["exclude_categories"])
        self.assertNotIn("Transport", ctx["analysis"]["expenses_by_category"])

    def test_income_analyzer_view_includes_exclude_categories_context(self) -> None:
        self.client.login(username="tester", password="pass")
        url = reverse("income_analyzer") + "?exclude_category=Side+Hustle"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        ctx = response.context
        self.assertIn("Side Hustle", ctx["exclude_categories"])
        self.assertNotIn("Side Hustle", ctx["analysis"]["income_by_category"])

    def test_expense_analyzer_view_multiple_excluded_categories(self) -> None:
        self.client.login(username="tester", password="pass")
        url = reverse("expense_analyzer") + "?exclude_category=Transport&exclude_category=Food"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        ctx = response.context
        self.assertEqual(len(ctx["exclude_categories"]), 2)
        self.assertEqual(ctx["analysis"]["total_expenses"], 0)

    def test_expense_analyzer_view_no_exclusions(self) -> None:
        self.client.login(username="tester", password="pass")
        url = reverse("expense_analyzer")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        ctx = response.context
        self.assertEqual(ctx["exclude_categories"], [])
        self.assertEqual(ctx["analysis"]["total_expenses"], 15000)

    def test_available_categories_in_context(self) -> None:
        self.client.login(username="tester", password="pass")
        url = reverse("expense_analyzer")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        ctx = response.context
        self.assertIn("available_categories", ctx)
        self.assertEqual(set(ctx["available_categories"]), {"Food", "Transport"})
