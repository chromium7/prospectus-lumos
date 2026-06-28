from __future__ import annotations

import tempfile
from tempfile import TemporaryDirectory
from unittest.mock import patch, MagicMock

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse

from libraries.google_cloud.tuples import File
from prospectus_lumos.apps.accounts.models import GoogleDriveCredentials, DocumentSource
from prospectus_lumos.apps.documents.models import Document
from prospectus_lumos.apps.expenses.services import ExpenseAnalyzerService, ExpenseSheetService
from prospectus_lumos.apps.transactions.models import Transaction


class ExpenseSheetServiceSyncTests(TestCase):
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
        self.assertEqual(txs.count(), 2)
        self.assertEqual({t.transaction_type for t in txs}, {"expense", "income"})

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
