from decimal import Decimal

from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.test import TestCase

from prospectus_lumos.apps.transactions.models import Transaction


class TransactionModelTests(TestCase):
    def test_manual_transaction_can_belong_directly_to_a_user(self) -> None:
        user = User.objects.create_user(username="transaction-owner", password="pass")

        entry = Transaction.objects.create(
            user=user,
            transaction_type=Transaction.TransactionType.EXPENSE,
            date="2026-10-02",
            amount=Decimal("125000.00"),
            description="Groceries",
            category="Food",
        )

        self.assertIsNone(entry.document)
        self.assertEqual(list(Transaction.for_user(user)), [entry])

    def test_transaction_requires_a_user_or_import_document(self) -> None:
        with self.assertRaises(IntegrityError), transaction.atomic():
            Transaction.objects.create(
                transaction_type=Transaction.TransactionType.INCOME,
                date="2026-10-02",
                amount=Decimal("500000.00"),
                description="Consulting",
                category="Freelance",
            )
