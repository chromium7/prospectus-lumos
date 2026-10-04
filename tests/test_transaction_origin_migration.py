from decimal import Decimal
from importlib import import_module
from typing import Any

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class TransactionOriginMigrationTests(TransactionTestCase):
    migrate_from = ("transactions", "0003_transaction_origin_nullable")
    migrate_to = ("transactions", "0005_enforce_transaction_origin")

    def setUp(self) -> None:
        super().setUp()
        self.executor = MigrationExecutor(connection)
        self.executor.migrate([self.migrate_from])
        self.old_apps = self.executor.loader.project_state([self.migrate_from]).apps

    def tearDown(self) -> None:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def _create_transaction(self, transaction_model: Any, **overrides: Any) -> Any:
        values = {
            "transaction_type": "expense",
            "date": "1/8/2025",
            "amount": Decimal("125000.00"),
            "description": "Historical row",
            "category": "Food",
        }
        values.update(overrides)
        return transaction_model.objects.create(**values)

    def test_backfill_classifies_known_lineage_and_reports_ambiguous_rows(self) -> None:
        user_model = self.old_apps.get_model("auth", "User")
        source_model = self.old_apps.get_model("accounts", "DocumentSource")
        document_model = self.old_apps.get_model("documents", "Document")
        transaction_model = self.old_apps.get_model("transactions", "Transaction")
        user = user_model.objects.create(username="migration-owner")

        manual_source = source_model.objects.create(user=user, source_type="manual", name="Manual")
        google_source = source_model.objects.create(user=user, source_type="google_drive", name="Google")
        upload_source = source_model.objects.create(user=user, source_type="direct_upload", name="Upload")
        ambiguous_source = source_model.objects.create(user=user, source_type="legacy_feed", name="Legacy")
        manual_document = document_model.objects.create(user=user, source=manual_source, month=1, year=2025)
        google_document = document_model.objects.create(user=user, source=google_source, month=2, year=2025)
        upload_document = document_model.objects.create(user=user, source=upload_source, month=3, year=2025)
        ambiguous_document = document_model.objects.create(user=user, source=ambiguous_source, month=4, year=2025)

        direct = self._create_transaction(transaction_model, user=user, description="Direct")
        manual = self._create_transaction(transaction_model, document=manual_document, description="Manual document")
        google = self._create_transaction(transaction_model, document=google_document, description="Google")
        upload = self._create_transaction(transaction_model, document=upload_document, description="Upload")
        ambiguous = self._create_transaction(
            transaction_model,
            document=ambiguous_document,
            description="Ambiguous",
        )

        migration = import_module("prospectus_lumos.apps.transactions.migrations.0004_backfill_transaction_origin")
        with self.assertRaisesMessage(
            RuntimeError,
            f"id={ambiguous.pk} (document source type 'legacy_feed')",
        ):
            migration.backfill_transaction_origins(self.old_apps, None)

        self.assertFalse(transaction_model.objects.exclude(origin__isnull=True).exists())

        ambiguous_source.source_type = "direct_upload"
        ambiguous_source.save(update_fields=["source_type"])
        self.executor = MigrationExecutor(connection)
        self.executor.migrate([self.migrate_to])
        migrated_apps = self.executor.loader.project_state([self.migrate_to]).apps
        migrated_transaction = migrated_apps.get_model("transactions", "Transaction")

        self.assertEqual(migrated_transaction.objects.get(pk=direct.pk).origin, "manual")
        self.assertEqual(migrated_transaction.objects.get(pk=manual.pk).origin, "manual")
        self.assertEqual(migrated_transaction.objects.get(pk=google.pk).origin, "google_sheets")
        self.assertEqual(migrated_transaction.objects.get(pk=upload.pk).origin, "direct_upload")
        self.assertEqual(migrated_transaction.objects.get(pk=ambiguous.pk).origin, "direct_upload")
