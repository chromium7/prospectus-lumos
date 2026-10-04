from decimal import Decimal
from typing import Any

from django.core.exceptions import FieldDoesNotExist
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder
from django.test import TransactionTestCase


class TransactionOriginMigrationTests(TransactionTestCase):
    migrate_before_origin = ("transactions", "0002_transaction_user_alter_transaction_document_and_more")
    migrate_nullable = ("transactions", "0003_transaction_origin_nullable")
    migrate_backfilled = ("transactions", "0004_backfill_transaction_origin")
    migrate_enforced = ("transactions", "0005_enforce_transaction_origin")

    def setUp(self) -> None:
        super().setUp()
        self.executor = MigrationExecutor(connection)
        self.executor.migrate([self.migrate_nullable])
        self.old_apps = self.executor.loader.project_state([self.migrate_nullable]).apps

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

    def _create_mixed_transactions(self) -> tuple[list[Any], dict[int, tuple[str, Decimal]]]:
        user_model = self.old_apps.get_model("auth", "User")
        source_model = self.old_apps.get_model("accounts", "DocumentSource")
        document_model = self.old_apps.get_model("documents", "Document")
        transaction_model = self.old_apps.get_model("transactions", "Transaction")
        user = user_model.objects.create(username="migration-owner")

        manual_source = source_model.objects.create(user=user, source_type="manual", name="Manual")
        google_source = source_model.objects.create(user=user, source_type="google_drive", name="Google")
        upload_source = source_model.objects.create(user=user, source_type="direct_upload", name="Upload")
        manual_document = document_model.objects.create(user=user, source=manual_source, month=1, year=2025)
        google_document = document_model.objects.create(user=user, source=google_source, month=2, year=2025)
        upload_document = document_model.objects.create(user=user, source=upload_source, month=3, year=2025)

        transactions = [
            self._create_transaction(
                transaction_model,
                user=user,
                date="2025-01-02",
                amount=Decimal("10.25"),
                description="Direct",
            ),
            self._create_transaction(
                transaction_model,
                document=manual_document,
                date="3/1/2025",
                amount=Decimal("20.50"),
                description="Manual document",
            ),
            self._create_transaction(
                transaction_model,
                document=google_document,
                date="4/2/2025",
                amount=Decimal("30.75"),
                description="Google",
            ),
            self._create_transaction(
                transaction_model,
                document=upload_document,
                date="5/3/2025",
                amount=Decimal("40.00"),
                description="Upload",
            ),
        ]
        preserved_values = {transaction.pk: (transaction.date, transaction.amount) for transaction in transactions}
        return transactions, preserved_values

    def test_full_migration_aborts_before_writes_and_reports_truncated_ambiguity(self) -> None:
        user_model = self.old_apps.get_model("auth", "User")
        source_model = self.old_apps.get_model("accounts", "DocumentSource")
        document_model = self.old_apps.get_model("documents", "Document")
        transaction_model = self.old_apps.get_model("transactions", "Transaction")
        user = user_model.objects.create(username="ambiguous-owner")
        ambiguous_source = source_model.objects.create(user=user, source_type="legacy_feed", name="Legacy")
        ambiguous_document = document_model.objects.create(user=user, source=ambiguous_source, month=4, year=2025)
        known = self._create_transaction(transaction_model, user=user, description="Known manual")
        ambiguous = [
            transaction_model(
                document=ambiguous_document,
                transaction_type="expense",
                date=f"{day}/4/2025",
                amount=Decimal("1.00"),
                description=f"Ambiguous {day}",
                category="Legacy",
            )
            for day in range(1, 22)
        ]
        transaction_model.objects.bulk_create(ambiguous)

        executor = MigrationExecutor(connection)
        with self.assertRaises(RuntimeError) as error:
            executor.migrate([self.migrate_enforced])

        ambiguous_source.source_type = "direct_upload"
        ambiguous_source.save(update_fields=["source_type"])
        self.assertIn("Cannot classify origin for 21 transaction(s)", str(error.exception))
        self.assertIn("and 1 more", str(error.exception))
        self.assertEqual(str(error.exception).count("document source type"), 20)
        self.assertIsNone(transaction_model.objects.get(pk=known.pk).origin)
        self.assertFalse(transaction_model.objects.exclude(origin__isnull=True).exists())
        applied = MigrationRecorder(connection).applied_migrations()
        self.assertIn(self.migrate_nullable, applied)
        self.assertNotIn(self.migrate_backfilled, applied)
        self.assertNotIn(self.migrate_enforced, applied)
        with connection.cursor() as cursor:
            columns = {
                column.name: column
                for column in connection.introspection.get_table_description(cursor, "transactions_transaction")
            }
        self.assertTrue(columns["origin"].null_ok)

    def test_forward_and_rollback_preserve_historical_transaction_values(self) -> None:
        transactions, preserved_values = self._create_mixed_transactions()

        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_enforced])
        enforced_apps = executor.loader.project_state([self.migrate_enforced]).apps
        enforced_transaction = enforced_apps.get_model("transactions", "Transaction")
        origins = {
            transaction.description: transaction.origin
            for transaction in enforced_transaction.objects.filter(pk__in=preserved_values)
        }
        self.assertEqual(
            origins,
            {
                "Direct": "manual",
                "Manual document": "manual",
                "Google": "google_sheets",
                "Upload": "direct_upload",
            },
        )

        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_backfilled])
        backfilled_apps = executor.loader.project_state([self.migrate_backfilled]).apps
        backfilled_transaction = backfilled_apps.get_model("transactions", "Transaction")
        self.assertTrue(backfilled_transaction._meta.get_field("origin").null)
        self.assertFalse(backfilled_transaction.objects.exclude(origin__isnull=True).exists())
        with connection.cursor() as cursor:
            columns = {
                column.name: column
                for column in connection.introspection.get_table_description(cursor, "transactions_transaction")
            }
        self.assertTrue(columns["origin"].null_ok)

        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_before_origin])
        before_apps = executor.loader.project_state([self.migrate_before_origin]).apps
        before_transaction = before_apps.get_model("transactions", "Transaction")
        with self.assertRaises(FieldDoesNotExist):
            before_transaction._meta.get_field("origin")

        rolled_back = {
            transaction.pk: (transaction.date, transaction.amount)
            for transaction in before_transaction.objects.filter(pk__in=preserved_values)
        }
        self.assertEqual(rolled_back, preserved_values)
        self.assertCountEqual(rolled_back, [transaction.pk for transaction in transactions])
        with connection.cursor() as cursor:
            column_names = {
                column.name
                for column in connection.introspection.get_table_description(cursor, "transactions_transaction")
            }
        self.assertNotIn("origin", column_names)
