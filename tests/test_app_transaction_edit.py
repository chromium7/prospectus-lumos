from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import patch

from django.contrib.auth.models import User
from django.db import OperationalError
from django.test import TestCase
from django.urls import reverse

from prospectus_lumos.apps.accounts.models import DocumentSource
from prospectus_lumos.apps.documents.models import Document
from prospectus_lumos.apps.documents.services import resolve_monthly_document
from prospectus_lumos.apps.transactions.models import Transaction
from prospectus_lumos.apps.transactions.services import TransactionEditConflict


class TransactionEditPageTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(username="edit-page-owner", password="pass")
        self.other_user = User.objects.create_user(username="edit-page-other", password="pass")
        self.september = resolve_monthly_document(user=self.user, year=2026, month=9)
        self.manual = Transaction.objects.create(
            user=self.user,
            document=self.september,
            transaction_type=Transaction.TransactionType.EXPENSE,
            date="2026-09-30",
            amount=Decimal("125000.50"),
            description="Weekly groceries",
            category="Food",
            origin=Transaction.Origin.MANUAL,
        )
        source = DocumentSource.objects.create(
            user=self.user,
            source_type=DocumentSource.SourceType.GOOGLE_DRIVE,
            name="Household budget sheet",
        )
        october = Document.objects.create(user=self.user, source=source, year=2026, month=10)
        self.imported = Transaction.objects.create(
            document=october,
            transaction_type=Transaction.TransactionType.INCOME,
            date="31/10/2026",
            amount=Decimal("1234567.00"),
            description="Imported salary",
            category="Income",
            origin=Transaction.Origin.GOOGLE_SHEETS,
        )
        self.url = reverse("app:transaction_edit", args=[self.manual.pk])

    def valid_payload(self, **overrides: str) -> dict[str, str]:
        payload = {
            "transaction_type": Transaction.TransactionType.INCOME,
            "amount": "225000.75",
            "category": "Side income",
            "date": "2026-10-01",
            "description": "Freelance work",
            "next": "/app/?month=2026-10",
            "save_action": "save",
        }
        payload.update(overrides)
        return payload

    def test_route_requires_login_and_hides_other_owners_rows(self) -> None:
        response = self.client.get(self.url)

        self.assertRedirects(response, f"{reverse('login')}?next={self.url}", fetch_redirect_response=False)

        other_document = resolve_monthly_document(user=self.other_user, year=2026, month=9)
        other_transaction = Transaction.objects.create(
            user=self.other_user,
            document=other_document,
            transaction_type=Transaction.TransactionType.EXPENSE,
            date="2026-09-01",
            amount=Decimal("50000.00"),
            description="Private expense",
            category="Private",
            origin=Transaction.Origin.MANUAL,
        )
        self.client.force_login(self.user)

        other_response = self.client.get(reverse("app:transaction_edit", args=[other_transaction.pk]))
        missing_response = self.client.get(reverse("app:transaction_edit", args=[999999]))

        self.assertEqual(other_response.status_code, 404)
        self.assertEqual(missing_response.status_code, 404)
        self.assertNotContains(other_response, "Private expense", status_code=404)

    def test_manual_transaction_renders_reused_accessible_form_with_initial_values(self) -> None:
        self.client.force_login(self.user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/transaction_form.html")
        self.assertContains(response, "Edit transaction")
        self.assertContains(response, "Manual")
        self.assertContains(response, "Editable here")
        self.assertContains(response, 'value="125000.50"')
        self.assertContains(response, 'value="2026-09-30"')
        self.assertContains(response, 'value="Weekly groceries"')
        self.assertContains(response, 'for="id_amount"')
        self.assertContains(response, 'for="id_date"')
        self.assertContains(response, "Save changes")
        self.assertNotContains(response, "Save and add another")
        self.assertContains(response, 'data-loading-text="Updating transaction…"')

    def test_valid_post_moves_month_updates_in_place_and_redirects_with_feedback(self) -> None:
        self.client.force_login(self.user)

        response = self.client.post(self.url, self.valid_payload(), follow=True)

        self.assertRedirects(response, "/app/?month=2026-10")
        self.assertContains(response, "Income updated.")
        self.manual.refresh_from_db()
        self.september.refresh_from_db()
        october = Document.objects.get(user=self.user, year=2026, month=10)
        self.assertEqual(self.manual.pk, Transaction.objects.get(description="Freelance work").pk)
        self.assertEqual(self.manual.document, october)
        self.assertEqual(self.manual.origin, Transaction.Origin.MANUAL)
        self.assertEqual(self.manual.date, "2026-10-01")
        self.assertEqual(self.manual.amount, Decimal("225000.75"))
        self.assertEqual(self.september.total_expenses, Decimal("0.00"))
        self.assertEqual(october.total_income, Decimal("1459567.75"))

    def test_invalid_post_preserves_submitted_values_and_original_row(self) -> None:
        self.client.force_login(self.user)

        response = self.client.post(
            self.url,
            self.valid_payload(
                amount="987654.32",
                category="",
                date="31/10/2026",
                description="Still visible",
            ),
        )

        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Choose or enter a category.", status_code=400)
        self.assertContains(response, "Enter a valid date.", status_code=400)
        self.assertContains(response, 'value="987654.32"', status_code=400)
        self.assertContains(response, 'value="31/10/2026"', status_code=400)
        self.assertContains(response, 'value="Still visible"', status_code=400)
        self.assertContains(response, "data-dirty", status_code=400)
        self.manual.refresh_from_db()
        self.assertEqual(self.manual.description, "Weekly groceries")
        self.assertEqual(self.manual.date, "2026-09-30")

    def test_imported_transaction_shows_source_managed_read_only_state_and_idr(self) -> None:
        self.client.force_login(self.user)
        imported_url = reverse("app:transaction_edit", args=[self.imported.pk])

        response = self.client.get(imported_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Google Sheets")
        self.assertContains(response, "Read only")
        self.assertContains(response, "managed by its import source")
        self.assertContains(response, "Rp1.234.567")
        self.assertContains(response, "31/10/2026")
        self.assertContains(response, "Household budget sheet")
        self.assertContains(response, "Review imported month")
        self.assertNotContains(response, "Save changes")
        self.assertNotContains(response, '<form class="card transaction-form')

    def test_imported_post_redirects_with_error_and_does_not_mutate_row(self) -> None:
        self.client.force_login(self.user)
        imported_url = reverse("app:transaction_edit", args=[self.imported.pk])

        response = self.client.post(
            imported_url,
            self.valid_payload(description="Attempted overwrite", next="/app/?month=2026-10"),
            follow=True,
        )

        self.assertRedirects(response, f"{imported_url}?next=%2Fapp%2F%3Fmonth%3D2026-10")
        self.assertContains(response, "This imported transaction is read-only here.")
        self.imported.refresh_from_db()
        self.assertEqual(self.imported.description, "Imported salary")
        self.assertEqual(self.imported.amount, Decimal("1234567.00"))

    @patch(
        "prospectus_lumos.website.app.forms.edit_manual_transaction",
        side_effect=OperationalError("database unavailable"),
    )
    def test_save_failure_preserves_values_and_shows_retryable_error(self, _edit: Any) -> None:
        self.client.force_login(self.user)

        with self.assertLogs("prospectus_lumos.website.app.views", level="ERROR") as logs:
            response = self.client.post(self.url, self.valid_payload(description="Unsaved edit"))

        self.assertEqual(response.status_code, 503)
        self.assertContains(response, "couldn&#x27;t update this transaction", status_code=503)
        self.assertContains(response, 'value="Unsaved edit"', status_code=503)
        self.assertIn("Manual transaction edit failed", logs.output[0])
        self.manual.refresh_from_db()
        self.assertEqual(self.manual.description, "Weekly groceries")

    @patch(
        "prospectus_lumos.website.app.forms.edit_manual_transaction",
        side_effect=TransactionEditConflict("The transaction moved; reload and try again."),
    )
    def test_edit_conflict_preserves_values_and_returns_conflict_status(self, _edit: Any) -> None:
        self.client.force_login(self.user)

        response = self.client.post(self.url, self.valid_payload(description="Conflicting edit"))

        self.assertEqual(response.status_code, 409)
        self.assertContains(response, "The transaction moved; reload and try again.", status_code=409)
        self.assertContains(response, 'value="Conflicting edit"', status_code=409)

    def test_unsafe_return_url_falls_back_to_the_owned_transaction_month(self) -> None:
        self.client.force_login(self.user)

        get_response = self.client.get(self.url, {"next": "https://example.com/phishing"})
        post_response = self.client.post(
            self.url,
            self.valid_payload(next="https://example.com/phishing", date="2026-09-29"),
        )

        self.assertContains(get_response, 'href="/app/?month=2026-09">Cancel</a>')
        self.assertRedirects(post_response, "/app/?month=2026-09", fetch_redirect_response=False)

    def test_edit_styles_cover_origin_read_only_and_narrow_layouts(self) -> None:
        stylesheet = Path("static_files/css/theme.css").read_text(encoding="utf-8")

        self.assertIn(".transaction-origin", stylesheet)
        self.assertIn(".transaction-read-only__details", stylesheet)
        self.assertIn(".transaction-origin__status--read-only", stylesheet)
        self.assertIn("@media (max-width: 47.99rem)", stylesheet)
        self.assertIn("grid-template-columns: 1fr", stylesheet)
