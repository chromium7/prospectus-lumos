from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from prospectus_lumos.apps.transactions.models import CategoryBudget
from prospectus_lumos.core.categories import category_key


class CategoryBudgetModelTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(username="budget-owner")

    def test_decimal_amount_timestamps_and_user_cascade(self) -> None:
        budget = CategoryBudget.objects.create(
            user=self.user, year=2026, month=10, category="Food", amount=Decimal("123456.78")
        )
        budget.refresh_from_db()
        self.assertEqual(budget.amount, Decimal("123456.78"))
        self.assertIsNotNone(budget.created_at)
        updated_at = budget.updated_at
        budget.amount = Decimal("0.00")
        budget.save(update_fields=["amount", "updated_at"])
        budget.refresh_from_db()
        self.assertEqual(budget.amount, Decimal("0.00"))
        self.assertGreater(budget.updated_at, updated_at)
        self.user.delete()
        self.assertFalse(CategoryBudget.objects.exists())

    def test_normalized_category_uniqueness_and_scope(self) -> None:
        budget = CategoryBudget.objects.create(user=self.user, year=2026, month=10, category=" Food ", amount=0)
        self.assertEqual(budget.category, "Food")
        duplicate = CategoryBudget(user=self.user, year=2026, month=10, category="\tFood\n", amount=0)
        with self.assertRaises(ValidationError):
            duplicate.full_clean()
        with self.assertRaises(IntegrityError), transaction.atomic():
            duplicate.save()
        other_user = User.objects.create_user(username="other-budget-owner")
        for overrides in ({"user": other_user}, {"year": 2027}, {"month": 11}, {"category": "food"}):
            with self.subTest(overrides=overrides):
                values = {"user": self.user, "year": 2026, "month": 10, "category": "Food", "amount": 0}
                values.update(overrides)
                CategoryBudget.objects.create(**values)

    def test_blank_categories_share_uncategorized_key(self) -> None:
        budget = CategoryBudget(user=self.user, year=2026, month=10, category=" \t\n", amount=0)
        budget.full_clean()
        budget.save()
        self.assertEqual(budget.category, "Uncategorized")
        for category in ("", "  ", "Uncategorized", " Uncategorized "):
            with self.subTest(category=category):
                self.assertEqual(category_key(category), "Uncategorized")
                with self.assertRaises(IntegrityError), transaction.atomic():
                    CategoryBudget.objects.create(user=self.user, year=2026, month=10, category=category, amount=0)
        self.assertEqual(category_key("  Food  and\tDrink  "), "Food  and\tDrink")

    def test_calendar_and_amount_constraints(self) -> None:
        for overrides in ({"year": 0}, {"year": 10000}, {"month": 0}, {"month": 13}, {"amount": Decimal("-0.01")}):
            with self.subTest(overrides=overrides):
                values = {"user": self.user, "year": 2026, "month": 10, "category": "Food", "amount": 0}
                values.update(overrides)
                budget = CategoryBudget(**values)
                with self.assertRaises(ValidationError):
                    budget.full_clean()
                with self.assertRaises(IntegrityError), transaction.atomic():
                    budget.save()

    def test_field_validation(self) -> None:
        for overrides in (
            {"amount": Decimal("0.001")},
            {"amount": Decimal("10000000000000.00")},
            {"category": "x" * 101},
            {"user": None},
        ):
            with self.subTest(overrides=overrides):
                values = {"user": self.user, "year": 2026, "month": 10, "category": "Food", "amount": 0}
                values.update(overrides)
                with self.assertRaises(ValidationError):
                    CategoryBudget(**values).full_clean()
