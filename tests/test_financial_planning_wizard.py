from __future__ import annotations

from datetime import date

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

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
        self.assertRedirects(response, reverse("freedom_plan_draft", args=(draft.plan_id,)))
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
        self.assertRedirects(response, reverse("freedom_plan_draft", args=(draft.plan_id,)))
        draft.refresh_from_db()
        draft.plan.refresh_from_db()
        self.assertEqual(draft.plan.name, "A simpler goal")
        self.assertEqual(draft.desired_monthly_lifestyle, 18000000)

        foreign_plan = FreedomPlan.objects.create(user=self.other, name="Private")
        self.assertEqual(self.client.get(reverse("freedom_plan_goal", args=(foreign_plan.pk,))).status_code, 404)
