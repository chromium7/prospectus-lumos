from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.db import OperationalError
from django.test import Client, TestCase
from django.urls import reverse

from prospectus_lumos.apps.documents.services import recalculate_document_summary, resolve_monthly_document
from prospectus_lumos.apps.transactions.models import Transaction
from prospectus_lumos.apps.transactions.services import TransactionDeleteConflict


class MonthlyDetailActionsTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(username="monthly-owner", password="pass")
        self.document = resolve_monthly_document(user=self.user, year=2026, month=10)
        self.manual = self.make_transaction("manual", "Groceries", "125000")
        self.imported = self.make_transaction("google_sheets", "Salary", "9000000", "income")
        self.uploaded = self.make_transaction("direct_upload", "Electricity", "300000")
        recalculate_document_summary(document=self.document)
        self.detail_url = reverse("document_detail", args=[self.document.pk])
        self.delete_url = reverse("transaction_delete", args=[self.document.pk, self.manual.pk])
        self.client.force_login(self.user)

    def make_transaction(self, origin: str, description: str, amount: str, kind: str = "expense") -> Transaction:
        return Transaction.objects.create(
            document=self.document,
            user=self.user if origin == "manual" else None,
            origin=origin,
            transaction_type=kind,
            date="2026-10-15",
            description=description,
            amount=Decimal(amount),
            category="Budget",
        )

    def test_mixed_rows_show_origin_and_source_appropriate_actions(self) -> None:
        response = self.client.get(self.detail_url)
        for label in ("Manual", "Google Sheets", "Direct Upload", "Groceries", "Salary", "Electricity"):
            self.assertContains(response, label)
        self.assertContains(response, 'aria-label="Edit: Groceries"')
        self.assertContains(response, 'aria-label="Delete: Groceries"')
        self.assertContains(response, 'aria-label="Source details: Salary"')
        self.assertNotContains(response, 'aria-label="Delete: Salary"')
        self.assertNotContains(response, 'aria-label="Edit: Electricity"')
        self.assertNotContains(response, "Download CSV")

    def test_origin_filter_includes_legacy_imports_without_direct_user(self) -> None:
        for origin, expected in (
            ("manual", self.manual),
            ("google_sheets", self.imported),
            ("direct_upload", self.uploaded),
        ):
            with self.subTest(origin=origin):
                response = self.client.get(self.detail_url, {"origin": origin})
                self.assertEqual(list(response.context["transactions"]), [expected])
                self.assertContains(response, f'value="{origin}" selected')
                # Summary cards continue to describe the entire month.
                self.assertEqual(response.context["document"].total_expenses, Decimal("425000"))

    def test_type_origin_and_amount_sort_compose_and_survive_sort_link(self) -> None:
        cheaper = self.make_transaction("manual", "Bus fare", "5000")
        self.make_transaction("manual", "Refund", "10000", "income")
        filters = {"origin": "manual", "transaction_type": "expense", "sort": "amount", "direction": "asc"}
        response = self.client.get(self.detail_url, filters)
        self.assertEqual(list(response.context["transactions"]), [cheaper, self.manual])
        self.assertContains(response, "direction=desc&transaction_type=expense&origin=manual")
        filters["direction"] = "desc"
        response = self.client.get(self.detail_url, filters)
        self.assertEqual(list(response.context["transactions"]), [self.manual, cheaper])
        self.assertContains(response, "direction=asc&transaction_type=expense&origin=manual")

    def test_no_matches_explains_filters_and_clear_restores_all_origins(self) -> None:
        response = self.client.get(self.detail_url, {"origin": "google_sheets", "transaction_type": "expense"})
        self.assertContains(response, "No transactions match these filters")
        self.assertNotContains(response, "This document does not contain any transactions yet")
        response = self.client.get(self.detail_url)
        self.assertEqual(set(response.context["transactions"]), {self.manual, self.imported, self.uploaded})

    def test_invalid_origin_reports_error_and_keeps_document_scope(self) -> None:
        other = User.objects.create_user(username="another-owner")
        other_document = resolve_monthly_document(user=other, year=2026, month=10)
        Transaction.objects.create(
            document=other_document,
            origin="google_sheets",
            transaction_type="expense",
            date="2026-10-01",
            description="Private activity",
            amount=1,
        )
        response = self.client.get(self.detail_url, {"origin": "unknown", "transaction_type": "income"})
        self.assertContains(response, 'role="alert"')
        self.assertContains(response, "Choose a valid filter")
        self.assertEqual(set(response.context["transactions"]), {self.manual, self.imported, self.uploaded})
        self.assertNotContains(response, "Private activity")
        self.assertEqual(self.client.get(reverse("document_detail", args=[other_document.pk])).status_code, 404)

    def test_month_navigation_and_type_filter_keep_imported_activity_accessible(self) -> None:
        response = self.client.get(reverse("document_list"), {"year": "2026", "month": "10"})
        self.assertContains(response, self.detail_url)
        self.assertContains(response, "Manual entries")
        self.assertNotContains(response, "Manual Upload")
        self.assertNotContains(response, reverse("download_csv", args=[self.document.pk]))
        response = self.client.get(self.detail_url, {"transaction_type": "expense"})
        self.assertEqual(set(response.context["transactions"]), {self.manual, self.uploaded})
        self.document.csv_file = "documents/csv/import.csv"
        self.document.save(update_fields=["csv_file"])
        response = self.client.get(reverse("document_list"))
        self.assertContains(response, reverse("download_csv", args=[self.document.pk]))

    def test_confirmation_get_is_safe_and_post_refreshes_month_and_dashboard(self) -> None:
        response = self.client.get(self.delete_url)
        self.assertContains(response, "Delete manual transaction?")
        self.assertContains(response, "Groceries")
        self.assertContains(response, "cannot be undone")
        self.assertTrue(Transaction.objects.filter(pk=self.manual.pk).exists())
        response = self.client.post(self.delete_url, follow=True)
        self.assertRedirects(response, self.detail_url)
        self.assertContains(response, "Manual transaction deleted.")
        self.assertContains(response, 'role="alert"')
        self.assertNotContains(response, "Groceries")
        self.document.refresh_from_db()
        self.assertEqual(self.document.total_expenses, Decimal("300000"))
        self.assertEqual(self.document.total_income, Decimal("9000000"))
        self.assertEqual(self.document.expenses_count, 1)
        dashboard = self.client.get(reverse("dashboard"))
        self.assertEqual(dashboard.context["total_expenses"], Decimal("300000"))
        self.assertEqual(dashboard.context["net_income"], Decimal("8700000"))
        self.assertTrue(Transaction.objects.filter(pk=self.imported.pk).exists())
        self.assertTrue(Transaction.objects.filter(pk=self.uploaded.pk).exists())

    def test_edit_returns_to_month_with_fresh_totals(self) -> None:
        response = self.client.post(
            reverse("app:transaction_edit", args=[self.manual.pk]),
            {
                "transaction_type": "expense",
                "date": "2026-10-16",
                "amount": "200000",
                "description": "Updated groceries",
                "category": "Food",
                "next": self.detail_url,
            },
            follow=True,
        )
        self.assertRedirects(response, self.detail_url)
        self.assertContains(response, "Expense updated.")
        self.assertContains(response, "Updated groceries")
        self.assertEqual(response.context["document"].total_expenses, Decimal("500000"))

    def test_imported_delete_requests_are_read_only(self) -> None:
        for transaction in (self.imported, self.uploaded):
            url = reverse("transaction_delete", args=[self.document.pk, transaction.pk])
            response = self.client.get(url)
            self.assertContains(response, "read-only here")
            self.assertNotContains(response, 'type="submit"')
            response = self.client.post(url)
            self.assertContains(response, "read-only here", status_code=409)
            self.assertTrue(Transaction.objects.filter(pk=transaction.pk).exists())

    def test_login_ownership_document_scope_and_http_methods(self) -> None:
        self.client.logout()
        self.assertEqual(self.client.get(self.delete_url).status_code, 302)
        self.assertEqual(self.client.post(self.delete_url).status_code, 302)
        other_user = User.objects.create_user(username="other-owner")
        self.client.force_login(other_user)
        self.assertEqual(self.client.get(self.delete_url).status_code, 404)
        self.assertEqual(self.client.post(self.delete_url).status_code, 404)
        self.client.force_login(self.user)
        other_month = resolve_monthly_document(user=self.user, year=2026, month=9)
        wrong_month_url = reverse("transaction_delete", args=[other_month.pk, self.manual.pk])
        self.assertEqual(self.client.post(wrong_month_url).status_code, 404)
        self.assertEqual(self.client.delete(self.delete_url).status_code, 405)
        self.assertTrue(Transaction.objects.filter(pk=self.manual.pk).exists())

    def test_delete_requires_csrf(self) -> None:
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(self.delete_url).status_code, 403)
        self.assertTrue(Transaction.objects.filter(pk=self.manual.pk).exists())

    @patch("prospectus_lumos.website.expenses.views.delete_manual_transaction")
    def test_conflict_and_database_error_show_retryable_accessible_errors(self, delete: MagicMock) -> None:
        for error, status, message in (
            (TransactionDeleteConflict("Transaction moved; reload and try again."), 409, "Transaction moved"),
            (OperationalError("private database details"), 503, "Please try again."),
        ):
            delete.side_effect = error
            response = self.client.post(self.delete_url)
            self.assertContains(response, message, status_code=status)
            self.assertContains(response, 'role="alert"', status_code=status)
            self.assertContains(response, "Delete transaction", status_code=status)
            self.assertNotContains(response, "private database details", status_code=status)
        self.assertTrue(Transaction.objects.filter(pk=self.manual.pk).exists())

    def test_deleting_last_row_keeps_empty_month(self) -> None:
        self.imported.delete()
        self.uploaded.delete()
        response = self.client.post(self.delete_url, follow=True)
        self.assertContains(response, "No Transactions Found")
        self.document.refresh_from_db()
        self.assertEqual(self.document.total_income, 0)
        self.assertEqual(self.document.total_expenses, 0)
        self.assertEqual(self.document.income_count, 0)
        self.assertEqual(self.document.expenses_count, 0)
