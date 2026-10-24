from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.db import OperationalError
from django.test import Client, TestCase
from django.urls import reverse

from prospectus_lumos.apps.documents.models import Document
from prospectus_lumos.apps.documents.services import resolve_monthly_document
from prospectus_lumos.apps.transactions.models import CategoryBudget, Transaction


class MonthlyBudgetPageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="budget-page-owner")
        self.other = User.objects.create_user(username="budget-page-other")
        self.url = reverse("app:budgets") + "?month=2026-10"
        self.client.force_login(self.user)

    def save_limit(self, amount="50", **extra):
        return self.client.post(
            self.url,
            {
                "prefix": "new",
                "new-category": "Food",
                "new-amount": amount,
                "action": "save",
                **extra,
            },
        )

    def test_authentication_csrf_and_methods(self):
        anonymous = Client()
        self.assertEqual(anonymous.get(self.url).status_code, 302)
        self.assertEqual(anonymous.post(self.url).status_code, 302)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        self.assertEqual(csrf_client.post(self.url, {}).status_code, 403)
        self.assertEqual(self.client.delete(self.url).status_code, 405)

    def test_create_update_remove_and_owner_month_isolation(self):
        other = CategoryBudget.objects.create(user=self.other, year=2026, month=10, category="Food", amount=20)
        self.assertRedirects(self.save_limit(), self.url)
        budget = CategoryBudget.objects.get(user=self.user)
        self.assertEqual(budget.amount, Decimal("50"))
        self.assertRedirects(self.save_limit("0", user=self.other.pk, month="2027-01"), self.url)
        budget.refresh_from_db()
        self.assertEqual(budget.amount, 0)
        self.assertContains(self.client.get(self.url), "Rp0")
        self.assertContains(self.client.get(reverse("app:budgets") + "?month=2026-11"), "No budgets or spending yet")
        response = self.client.post(
            self.url,
            {
                "prefix": "row-0",
                "row-0-category": "Food",
                "row-0-amount": "bad",
                "action": "remove",
            },
        )
        self.assertRedirects(response, self.url)
        self.assertFalse(CategoryBudget.objects.filter(user=self.user).exists())
        self.assertTrue(CategoryBudget.objects.filter(pk=other.pk).exists())
        self.assertFalse(Document.objects.exists())

    def test_validation_retains_entries_and_invalid_month_never_writes(self):
        self.save_limit()
        for amount in ["-1", "0.001", "NaN", "Infinity", "10000000000000"]:
            self.assertEqual(self.save_limit(amount).status_code, 400)
        response = self.save_limit("-1")
        self.assertContains(response, 'value="Food"', status_code=400)
        self.assertEqual(CategoryBudget.objects.get(user=self.user).amount, 50)
        for month in ["0000-01", "10000-01", "2026-13", "garbage"]:
            response = self.client.post(reverse("app:budgets") + f"?month={month}", {})
            self.assertEqual(response.status_code, 400)
        self.assertEqual(CategoryBudget.objects.count(), 1)

    def test_month_edges_and_navigation(self):
        response = self.client.get(reverse("app:budgets") + "?month=2026-01")
        self.assertContains(response, "month=2025-12")
        self.assertContains(response, "month=2026-02")
        for month in ["0001-01", "9999-12"]:
            self.assertEqual(self.client.get(reverse("app:budgets") + f"?month={month}").status_code, 200)

    def test_rows_show_actual_remaining_over_and_unbudgeted_spending(self):
        document = resolve_monthly_document(user=self.user, year=2026, month=10)
        for category, amount in [("Food", "70"), ("Travel", "10"), ("No limit", "15")]:
            Transaction.objects.create(
                document=document,
                category=category,
                amount=amount,
                transaction_type="expense",
                description="Monthly expense",
                date="2026-10-01",
            )
        for category in ["Food", "Travel", "Unused"]:
            CategoryBudget.objects.create(user=self.user, year=2026, month=10, category=category, amount=50)
        response = self.client.get(self.url)
        self.assertContains(response, "Over budget")
        self.assertContains(response, "Rp20")
        self.assertContains(response, "Rp40")
        self.assertContains(response, "No limit")
        self.assertContains(response, "Unused")
        self.assertContains(response, "Rp0")

    def test_database_errors_are_visible_and_preserve_form(self):
        with patch("prospectus_lumos.website.app.budgets.upsert_category_budget", side_effect=OperationalError):
            response = self.save_limit()
        self.assertContains(response, "Your entries are still here", status_code=503)
        self.assertContains(response, 'value="Food"', status_code=503)
        with patch("prospectus_lumos.website.app.budgets.get_monthly_budget_actuals", side_effect=OperationalError):
            response = self.client.get(self.url)
        self.assertContains(response, "couldn&#x27;t load", status_code=503)
        self.assertNotContains(response, "No budgets or spending yet", status_code=503)
