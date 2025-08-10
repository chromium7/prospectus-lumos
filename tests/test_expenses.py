from __future__ import annotations

from tempfile import TemporaryDirectory
from unittest.mock import patch, MagicMock

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse

from libraries.google_cloud.tuples import File
from prospectus_lumos.apps.accounts.models import GoogleDriveCredentials, DocumentSource
from prospectus_lumos.apps.documents.models import Document
from prospectus_lumos.apps.expenses.services import ExpenseSheetService
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
