from __future__ import annotations

from datetime import date

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from prospectus_lumos.apps.accounts.models import DocumentSource
from prospectus_lumos.apps.documents.models import Document
from prospectus_lumos.apps.financial_planning.models import FreedomPlan, FreedomScenario


def _goal_payload(**overrides: object) -> dict[str, object]:
    today = timezone.localdate()
    values: dict[str, object] = {
        "name": "More choices by 45",
        "description": "Spend more time with family",
        "calculation_date": today.isoformat(),
        "birth_date": "1990-05-10",
        "target_date": date(today.year + 20, 1, 1).isoformat(),
        "desired_monthly_lifestyle": "20000000",
        "post_freedom_monthly_income": "3000000",
    }
    values.update(overrides)
    return values


def _money_payload(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "source_mode": "manual",
        "source_period_start": "",
        "source_period_end": "",
        "source_month_count": "0",
        "source_excluded_income_categories": "[]",
        "source_excluded_expense_categories": "[]",
        "current_monthly_income": "30000000",
        "current_monthly_expenses": "15000000",
        "current_monthly_investment": "10000000",
        "current_investable_assets": "100000000",
        "emergency_savings": "50000000",
    }
    values.update(overrides)
    return values


class FinancialPlanningWizardTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(username="wizard", password="pass")
        self.other = User.objects.create_user(username="other-wizard", password="pass")
        self.client.login(username="wizard", password="pass")

    def test_goal_page_uses_plain_language_and_creates_complete_draft(self) -> None:
        response = self.client.get(reverse("freedom_plan_create"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "financial_planning/wizard_goal.html")
        self.assertContains(response, "What would financial freedom look like for you?")
        self.assertContains(response, "A rough answer is enough")
        self.assertContains(response, "Step 1 of 4")

        response = self.client.post(reverse("freedom_plan_create"), _goal_payload())
        draft = FreedomScenario.objects.get(plan__user=self.user)
        self.assertRedirects(response, reverse("freedom_plan_money", args=(draft.plan_id,)))
        self.assertEqual(draft.plan.name, "More choices by 45")
        self.assertEqual(draft.desired_monthly_lifestyle, 20000000)
        self.assertEqual(draft.post_freedom_monthly_income, 3000000)
        self.assertEqual(draft.withdrawal_rate, 4)
        self.assertEqual(draft.annual_return_rate, 7)

    def test_goal_page_preserves_errors_and_only_edits_owned_drafts(self) -> None:
        response = self.client.post(
            reverse("freedom_plan_create"),
            _goal_payload(target_date=timezone.localdate().isoformat(), desired_monthly_lifestyle="12345678"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Choose a date in the future")
        self.assertContains(response, "12345678")

        self.client.post(reverse("freedom_plan_create"), _goal_payload())
        draft = FreedomScenario.objects.get(plan__user=self.user)
        response = self.client.post(
            reverse("freedom_plan_goal", args=(draft.plan_id,)),
            _goal_payload(name="A simpler goal", desired_monthly_lifestyle="18000000"),
        )
        self.assertRedirects(response, reverse("freedom_plan_money", args=(draft.plan_id,)))
        draft.refresh_from_db()
        draft.plan.refresh_from_db()
        self.assertEqual(draft.plan.name, "A simpler goal")
        self.assertEqual(draft.desired_monthly_lifestyle, 18000000)

        foreign_plan = FreedomPlan.objects.create(user=self.other, name="Private")
        self.assertEqual(self.client.get(reverse("freedom_plan_goal", args=(foreign_plan.pk,))).status_code, 404)

    def test_money_page_saves_plain_language_manual_answers(self) -> None:
        self.client.post(reverse("freedom_plan_create"), _goal_payload())
        draft = FreedomScenario.objects.get(plan__user=self.user)
        url = reverse("freedom_plan_money", args=(draft.plan_id,))

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "financial_planning/wizard_money.html")
        self.assertContains(response, "What does a normal month look like?")
        self.assertContains(response, "Estimates are fine")

        response = self.client.post(url, _money_payload())
        self.assertRedirects(response, reverse("freedom_plan_draft", args=(draft.plan_id,)))
        draft.refresh_from_db()
        self.assertEqual(draft.current_monthly_income, 30000000)
        self.assertEqual(draft.current_monthly_expenses, 15000000)
        self.assertEqual(draft.emergency_savings, 50000000)

    def test_money_page_imports_owned_actuals_then_allows_overrides(self) -> None:
        source = DocumentSource.objects.create(
            user=self.user,
            source_type=DocumentSource.SourceType.DIRECT_UPLOAD,
            name="Budget",
        )
        Document.objects.create(
            user=self.user,
            source=source,
            month=8,
            year=2026,
            total_income=30000000,
            total_expenses=15000000,
        )
        create_url = reverse("freedom_plan_create") + "?source=tracked_actuals&period=3m"
        response = self.client.post(create_url, _goal_payload())
        draft = FreedomScenario.objects.get(plan__user=self.user)
        money_url = reverse("freedom_plan_money", args=(draft.plan_id,))
        self.assertRedirects(response, money_url + "?source=tracked_actuals&period=3m")

        response = self.client.get(response.headers["Location"])
        self.assertContains(response, "1 represented complete month")
        self.assertContains(response, 'value="30000000.00"')

        response = self.client.post(
            money_url,
            _money_payload(
                source_mode="tracked_actuals",
                source_period_start="2026-06-01",
                source_period_end="2026-08-01",
                source_month_count="1",
                current_monthly_income="31000000",
                current_monthly_expenses="14000000",
            ),
        )
        self.assertEqual(response.status_code, 302)
        draft.refresh_from_db()
        self.assertEqual(draft.source_mode, FreedomScenario.SourceMode.TRACKED_ACTUALS)
        self.assertEqual(draft.source_month_count, 1)
        self.assertEqual(draft.current_monthly_income, 31000000)
        self.assertEqual(draft.current_monthly_expenses, 14000000)

    def test_portfolio_analyzer_links_its_owned_period_into_the_wizard(self) -> None:
        response = self.client.get(reverse("portfolio_analyzer") + "?year_from=2025&year_to=2026")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Turn this into a simple plan")
        self.assertContains(
            response,
            "source=tracked_actuals&amp;period=custom&amp;start_year=2025&amp;end_year=2026",
        )
