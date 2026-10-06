from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase

from prospectus_lumos.apps.accounts.models import DocumentSource
from prospectus_lumos.apps.documents.models import Document
from prospectus_lumos.apps.documents.services import resolve_monthly_document
from prospectus_lumos.apps.transactions.models import Transaction
from prospectus_lumos.apps.transactions.services import create_manual_transaction


class ManualTransactionCreationTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(username="manual-owner", password="pass")

    def _create(self, **overrides: object) -> Transaction:
        values = {
            "user": self.user,
            "transaction_type": Transaction.TransactionType.EXPENSE,
            "date": date(2026, 9, 30),
            "amount": Decimal("125000.50"),
            "description": "  Weekly groceries  ",
            "category": "  Food  ",
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

    def test_validates_type_date_amount_and_text_fields(self) -> None:
        invalid_values = (
            ("transaction_type", "transfer"),
            ("date", "2026-09-30"),
            ("date", datetime(2026, 9, 30, 12, 0)),
            ("amount", Decimal("0")),
            ("amount", Decimal("-1")),
            ("amount", Decimal("1.001")),
            ("amount", Decimal("10000000000000.00")),
            ("amount", Decimal("NaN")),
            ("amount", "125000.00"),
            ("description", "   "),
            ("description", "x" * 501),
            ("description", "invalid\x00description"),
            ("category", "   "),
            ("category", "x" * 101),
            ("category", "invalid\x00category"),
        )

        for field, value in invalid_values:
            with self.subTest(field=field, value=value), self.assertRaises(ValidationError):
                self._create(**{field: value})

        self.assertFalse(Transaction.objects.exists())
        self.assertFalse(Document.objects.exists())
        self.assertFalse(DocumentSource.objects.exists())

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
