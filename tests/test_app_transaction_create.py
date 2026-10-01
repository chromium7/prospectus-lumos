from datetime import date
from decimal import Decimal
from typing import Any

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from prospectus_lumos.apps.ledger.models import Category, FinancialAccount, LedgerTransaction


class TransactionCreatePageTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(username="transaction-owner", password="pass")
        self.other = User.objects.create_user(username="transaction-other", password="pass")
        self.url = reverse("app:transaction_create")

    def _account(self, *, user: User | None = None, name: str = "Wallet") -> FinancialAccount:
        return FinancialAccount.objects.create(
            user=user or self.user,
            name=name,
            type=FinancialAccount.Type.CASH,
            opening_balance=Decimal("500000.00"),
            opening_balance_date=date(2026, 1, 1),
        )

    def _open_form(self) -> tuple[Any, str]:
        response = self.client.get(self.url)
        token = response.context["form"]["submission_token"].value()
        return response, token

    def test_route_requires_session_login_and_empty_page_prompts_for_account(self) -> None:
        response = self.client.get(self.url)

        self.assertRedirects(response, f"{reverse('login')}?next={self.url}", fetch_redirect_response=False)

        self.client.force_login(self.user)
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/transaction_form.html")
        self.assertContains(response, "Add an account first")
        self.assertNotContains(response, '<form class="card transaction-form')

    def test_get_seeds_categories_and_renders_accessible_amount_first_form(self) -> None:
        self._account()
        self.client.force_login(self.user)

        response, token = self._open_form()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(token)
        self.assertEqual(Category.objects.filter(user=self.user).count(), 16)
        self.assertContains(response, "Expense")
        self.assertContains(response, "Income")
        self.assertContains(response, "Transfer")
        self.assertContains(response, 'name="type"', count=3)
        self.assertContains(response, "autofocus")
        self.assertContains(response, "Save and add another")
        self.assertContains(response, "reconnect before trying again")

    def test_get_renders_recent_account_and_category_shortcuts_as_optional_enhancements(self) -> None:
        account = self._account()
        category = Category.objects.create(user=self.user, name="Coffee", type=Category.Type.EXPENSE)
        LedgerTransaction.objects.create(
            user=self.user,
            type=LedgerTransaction.Type.EXPENSE,
            amount=Decimal("45000.00"),
            occurred_on=date(2026, 9, 30),
            account=account,
            category=category,
        )
        self.client.force_login(self.user)

        response, _ = self._open_form()

        self.assertContains(response, 'aria-label="Recently used accounts"')
        self.assertContains(response, f'data-select-value="{account.pk}"')
        self.assertContains(response, 'aria-label="Recently used categories"')
        self.assertContains(response, 'data-shortcut-category-type="expense"')

    def test_invalid_post_preserves_values_and_shows_summary_and_field_errors(self) -> None:
        account = self._account()
        self.client.force_login(self.user)
        _, token = self._open_form()

        response = self.client.post(
            self.url,
            {
                "submission_token": token,
                "type": LedgerTransaction.Type.EXPENSE,
                "amount": "125000.50",
                "account": account.pk,
                "category": "",
                "transfer_account": "",
                "occurred_on": "2026-09-30",
                "payee": "Market",
                "note": "Weekly groceries",
                "save_action": "save",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Check the details below", status_code=400)
        self.assertContains(response, "Choose a category.", status_code=400)
        self.assertContains(response, 'value="125000.50"', status_code=400)
        self.assertContains(response, 'value="Market"', status_code=400)
        self.assertFalse(LedgerTransaction.objects.exists())

    def test_expense_post_redirects_announces_total_and_replay_does_not_duplicate(self) -> None:
        account = self._account()
        self.client.force_login(self.user)
        _, token = self._open_form()
        category = Category.objects.get(user=self.user, name="Groceries", type=Category.Type.EXPENSE)
        payload = {
            "submission_token": token,
            "type": LedgerTransaction.Type.EXPENSE,
            "amount": "125000",
            "account": account.pk,
            "category": category.pk,
            "transfer_account": "",
            "occurred_on": "2026-09-30",
            "payee": "Market",
            "note": "Weekly groceries",
            "next": "/app/?month=2026-09",
            "save_action": "save",
        }

        response = self.client.post(self.url, payload)

        self.assertRedirects(response, "/app/?month=2026-09", fetch_redirect_response=False)
        transaction = LedgerTransaction.objects.get()
        self.assertEqual(transaction.user, self.user)
        self.assertEqual(transaction.amount, Decimal("125000.00"))
        self.assertEqual(transaction.category, category)

        replay = self.client.post(self.url, payload, follow=True)

        self.assertEqual(LedgerTransaction.objects.count(), 1)
        self.assertContains(replay, "That transaction was already saved. No duplicate was created.")

    def test_income_requires_income_category_and_rejects_another_users_objects(self) -> None:
        account = self._account()
        other_account = self._account(user=self.other, name="Private")
        self.client.force_login(self.user)
        _, token = self._open_form()
        expense_category = Category.objects.get(user=self.user, name="Groceries", type=Category.Type.EXPENSE)

        response = self.client.post(
            self.url,
            {
                "submission_token": token,
                "type": LedgerTransaction.Type.INCOME,
                "amount": "200000",
                "account": other_account.pk,
                "category": expense_category.pk,
                "occurred_on": "2026-09-30",
                "save_action": "save",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Select a valid choice", status_code=400)
        self.assertContains(response, "Choose an income category.", status_code=400)
        self.assertFalse(LedgerTransaction.objects.exists())
        self.assertEqual(response.context["form"].fields["account"].queryset.get(), account)

    def test_transfer_requires_different_destination_and_save_another_uses_prg(self) -> None:
        source = self._account(name="Wallet")
        destination = self._account(name="Bank")
        self.client.force_login(self.user)
        _, token = self._open_form()

        invalid = self.client.post(
            self.url,
            {
                "submission_token": token,
                "type": LedgerTransaction.Type.TRANSFER,
                "amount": "100000",
                "account": source.pk,
                "transfer_account": source.pk,
                "category": "",
                "occurred_on": "2026-10-01",
                "save_action": "save_another",
            },
        )

        self.assertEqual(invalid.status_code, 400)
        self.assertContains(invalid, "Choose a different destination account.", status_code=400)

        valid = self.client.post(
            self.url,
            {
                "submission_token": token,
                "type": LedgerTransaction.Type.TRANSFER,
                "amount": "100000",
                "account": source.pk,
                "transfer_account": destination.pk,
                "category": "",
                "occurred_on": "2026-10-01",
                "save_action": "save_another",
            },
        )

        self.assertRedirects(valid, self.url, fetch_redirect_response=False)
        transaction = LedgerTransaction.objects.get()
        self.assertEqual(transaction.transfer_account, destination)
        self.assertIsNone(transaction.category)

    def test_unsafe_return_url_falls_back_to_dashboard(self) -> None:
        account = self._account()
        self.client.force_login(self.user)
        _, token = self._open_form()
        category = Category.objects.get(user=self.user, name="Salary", type=Category.Type.INCOME)

        response = self.client.post(
            self.url,
            {
                "submission_token": token,
                "type": LedgerTransaction.Type.INCOME,
                "amount": "7500000",
                "account": account.pk,
                "category": category.pk,
                "occurred_on": "2026-10-01",
                "next": "https://example.com/phishing",
                "save_action": "save",
            },
        )

        self.assertRedirects(response, reverse("app:home"), fetch_redirect_response=False)
