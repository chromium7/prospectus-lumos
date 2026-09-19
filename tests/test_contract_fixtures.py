from __future__ import annotations

import json
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from prospectus_lumos.apps.documents.models import Document
from prospectus_lumos.apps.transactions.models import Transaction
from tests.contract_fixtures import CONTRACT_MONTHS, TENANT_A, TENANT_B, EntryKind, build_legacy_dataset


class ContractFixtureTests(TestCase):
    def test_dataset_covers_the_contract_cases(self) -> None:
        kinds = {entry.kind for month in CONTRACT_MONTHS for entry in month.entries}
        self.assertEqual(
            kinds,
            {EntryKind.INCOME, EntryKind.EXPENSE, EntryKind.TRANSFER_OUT, EntryKind.TRANSFER_IN},
        )
        entries = [entry for month in CONTRACT_MONTHS for entry in month.entries]
        self.assertTrue(any(entry.pending for entry in entries))
        self.assertTrue(any(entry.deleted for entry in entries))
        self.assertTrue(any(entry.archived_category for entry in entries))
        self.assertEqual({month.username for month in CONTRACT_MONTHS}, {TENANT_A, TENANT_B})
        self.assertTrue(all(isinstance(entry.amount, Decimal) for entry in entries))

    def test_build_legacy_dataset_materialises_only_legacy_rows(self) -> None:
        users = build_legacy_dataset()

        self.assertEqual(set(users), {TENANT_A, TENANT_B})
        self.assertEqual(Document.objects.count(), len(CONTRACT_MONTHS))
        self.assertEqual(
            Transaction.objects.count(),
            sum(len(month.legacy_entries) for month in CONTRACT_MONTHS),
        )
        # Transfers and deleted rows have no legacy representation.
        self.assertFalse(Transaction.objects.filter(description="To savings").exists())
        self.assertFalse(Transaction.objects.filter(description="Removed duplicate").exists())

        january = Document.objects.get(user=users[TENANT_A], year=2025, month=1)
        self.assertEqual(january.total_income, Decimal("12500000.00"))
        self.assertEqual(january.total_expenses, Decimal("2099500.50"))
        self.assertEqual(january.income_count, 1)
        self.assertEqual(january.expenses_count, 3)


class ReportLegacyBaselineTests(TestCase):
    def setUp(self) -> None:
        self.users = build_legacy_dataset()

    def _run(self, **kwargs: str) -> str:
        out = StringIO()
        call_command("report_legacy_baseline", stdout=out, **kwargs)
        return out.getvalue()

    def test_report_is_read_only_and_matches_document_totals(self) -> None:
        before = list(Transaction.objects.order_by("id").values_list("id", "amount"))

        payload = json.loads(self._run(format="json"))["months"]

        self.assertEqual(len(payload), len(CONTRACT_MONTHS))
        self.assertTrue(all(month["matches_document_totals"] for month in payload))
        january = next(
            month
            for month in payload
            if month["username"] == TENANT_A and month["year"] == 2025 and month["month"] == 1
        )
        self.assertEqual(january["income"], "12500000.00")
        self.assertEqual(january["expenses"], "2099500.50")
        self.assertEqual(january["net"], "10400499.50")
        self.assertEqual(january["expense_count"], 3)
        self.assertEqual(list(Transaction.objects.order_by("id").values_list("id", "amount")), before)
        self.assertEqual(Document.objects.count(), len(CONTRACT_MONTHS))

    def test_report_filters_by_user_and_reports_drift(self) -> None:
        text = self._run(user=TENANT_B)
        self.assertIn(TENANT_B, text)
        self.assertNotIn(TENANT_A, text)
        self.assertIn("1 user-month rows reported.", text)

        document = Document.objects.get(user=self.users[TENANT_B], year=2025, month=1)
        document.total_income = Decimal("1.00")
        document.save(update_fields=["total_income"])

        self.assertIn("MISMATCH", self._run(user=TENANT_B))
