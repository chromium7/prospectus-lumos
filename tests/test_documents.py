from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from prospectus_lumos.apps.accounts.models import DocumentSource
from prospectus_lumos.apps.documents.models import Document
from prospectus_lumos.apps.documents.services import MANUAL_SOURCE_NAME, resolve_monthly_document


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
