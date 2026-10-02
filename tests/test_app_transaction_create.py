from decimal import Decimal
from typing import Any

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from prospectus_lumos.apps.transactions.models import Transaction


class TransactionCreatePageTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(username="transaction-owner", password="pass")
        self.other = User.objects.create_user(username="transaction-other", password="pass")
        self.url = reverse("app:transaction_create")

    def open_form(self) -> tuple[Any, str]:
        """Open the form and return its response and submission token."""

        response = self.client.get(self.url)
        token = response.context["form"]["submission_token"].value()
        return response, token

    def test_route_requires_login_and_renders_without_an_account(self) -> None:
        response = self.client.get(self.url)

        self.assertRedirects(response, f"{reverse('login')}?next={self.url}", fetch_redirect_response=False)

        self.client.force_login(self.user)
        response, token = self.open_form()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(token)
        self.assertTemplateUsed(response, "app/transaction_form.html")
        self.assertContains(response, "Expense")
        self.assertContains(response, "Income")
        self.assertContains(response, 'type="radio" name="transaction_type"', count=2)
        self.assertContains(response, "without tracking an account")
        self.assertNotContains(response, "Financial account")
        self.assertNotContains(response, "transaction-form.js")

    def test_recent_category_suggestions_are_user_scoped(self) -> None:
        Transaction.objects.create(
            user=self.user,
            transaction_type=Transaction.TransactionType.EXPENSE,
            date="2026-10-01",
            amount=Decimal("45000.00"),
            description="Coffee",
            category="Food",
        )
        Transaction.objects.create(
            user=self.other,
            transaction_type=Transaction.TransactionType.INCOME,
            date="2026-10-01",
            amount=Decimal("900000.00"),
            description="Private work",
            category="Private category",
        )
        self.client.force_login(self.user)

        response, _ = self.open_form()

        self.assertContains(response, '<option value="Food">')
        self.assertNotContains(response, "Private category")

    def test_invalid_post_preserves_values_and_shows_errors(self) -> None:
        self.client.force_login(self.user)
        _, token = self.open_form()

        response = self.client.post(
            self.url,
            {
                "submission_token": token,
                "transaction_type": Transaction.TransactionType.EXPENSE,
                "amount": "125000.50",
                "category": "",
                "date": "2026-09-30",
                "description": "Weekly groceries",
                "save_action": "save",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Check the details below", status_code=400)
        self.assertContains(response, "Choose or enter a category.", status_code=400)
        self.assertContains(response, 'value="125000.50"', status_code=400)
        self.assertContains(response, 'value="Weekly groceries"', status_code=400)
        self.assertFalse(Transaction.objects.exists())

    def test_post_saves_direct_transaction_and_replay_does_not_duplicate(self) -> None:
        self.client.force_login(self.user)
        _, token = self.open_form()
        payload = {
            "submission_token": token,
            "transaction_type": Transaction.TransactionType.EXPENSE,
            "amount": "125000",
            "category": "Groceries",
            "date": "2026-09-30",
            "description": "Weekly groceries",
            "next": "/app/?month=2026-09",
            "save_action": "save",
        }

        response = self.client.post(self.url, payload)

        self.assertRedirects(response, "/app/?month=2026-09", fetch_redirect_response=False)
        saved = Transaction.objects.get()
        self.assertEqual(saved.user, self.user)
        self.assertIsNone(saved.document)
        self.assertEqual(saved.amount, Decimal("125000.00"))
        self.assertEqual(saved.category, "Groceries")
        self.assertEqual(saved.date, "2026-09-30")

        replay = self.client.post(self.url, payload, follow=True)

        self.assertEqual(Transaction.objects.count(), 1)
        self.assertContains(replay, "That transaction was already saved. No duplicate was created.")

    def test_transfer_type_is_not_accepted(self) -> None:
        self.client.force_login(self.user)
        _, token = self.open_form()

        response = self.client.post(
            self.url,
            {
                "submission_token": token,
                "transaction_type": "transfer",
                "amount": "100000",
                "category": "Transfer",
                "date": "2026-10-01",
                "description": "Move money",
                "save_action": "save",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Select a valid choice", status_code=400)
        self.assertFalse(Transaction.objects.exists())

    def test_save_another_and_unsafe_return_url_use_safe_redirects(self) -> None:
        self.client.force_login(self.user)
        _, token = self.open_form()
        payload = {
            "submission_token": token,
            "transaction_type": Transaction.TransactionType.INCOME,
            "amount": "7500000",
            "category": "Salary",
            "date": "2026-10-01",
            "description": "Monthly salary",
            "next": "https://example.com/phishing",
            "save_action": "save_another",
        }

        save_another = self.client.post(self.url, payload)

        self.assertRedirects(save_another, self.url, fetch_redirect_response=False)

        _, second_token = self.open_form()
        payload["submission_token"] = second_token
        payload["save_action"] = "save"
        fallback = self.client.post(self.url, payload)

        self.assertRedirects(fallback, reverse("app:home"), fetch_redirect_response=False)
