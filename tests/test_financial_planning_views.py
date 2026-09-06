from __future__ import annotations

from datetime import date

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from prospectus_lumos.apps.financial_planning.models import FreedomPlan, FreedomScenario


def _payload(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "name": "Freedom at 45",
        "description": "Manual plan",
        "calculation_date": "2026-09-04",
        "birth_date": "",
        "target_date": "2046-09-01",
        "current_monthly_income": "30000000",
        "current_monthly_expenses": "15000000",
        "current_monthly_investment": "10000000",
        "current_investable_assets": "100000000",
        "emergency_savings": "50000000",
        "desired_monthly_lifestyle": "20000000",
        "post_freedom_monthly_income": "0",
        "withdrawal_rate": "4",
        "annual_return_rate": "7",
        "annual_inflation_rate": "3",
        "annual_income_growth_rate": "0",
        "annual_contribution_growth_rate": "0",
        "safety_buffer_rate": "10",
    }
    values.update(overrides)
    return values


class FinancialPlanningViewTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(username="planner", password="pass")
        self.other = User.objects.create_user(username="other", password="pass")

    def _create_draft(self) -> FreedomScenario:
        self.client.login(username="planner", password="pass")
        response = self.client.post(reverse("freedom_plan_create"), _payload())
        self.assertEqual(response.status_code, 302)
        return FreedomScenario.objects.get(plan__user=self.user)

    def test_every_route_requires_authentication(self) -> None:
        plan = FreedomPlan.objects.create(user=self.user, name="Private")
        scenario = FreedomScenario.objects.create(
            plan=plan,
            calculation_date=date(2026, 9, 4),
            target_date=date(2046, 9, 1),
            desired_monthly_lifestyle=20000000,
        )
        urls = (
            reverse("freedom_plan_list"),
            reverse("freedom_plan_create"),
            reverse("freedom_plan_draft", args=(plan.pk,)),
            reverse("freedom_plan_save", args=(plan.pk,)),
            reverse("freedom_scenario_detail", args=(plan.pk, scenario.pk)),
            reverse("freedom_scenario_update", args=(plan.pk, scenario.pk)),
            reverse("freedom_scenario_duplicate", args=(plan.pk, scenario.pk)),
            reverse("freedom_plan_rename", args=(plan.pk,)),
            reverse("freedom_plan_archive", args=(plan.pk,)),
            reverse("freedom_plan_restore", args=(plan.pk,)),
        )
        for url in urls:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302)
            self.assertIn(reverse("login"), response.headers["Location"])

    def test_manual_create_recalculate_and_save_ignores_posted_outputs(self) -> None:
        draft = self._create_draft()
        response = self.client.get(reverse("freedom_plan_draft", args=(draft.plan_id,)))
        self.assertTemplateUsed(response, "financial_planning/plan_builder.html")

        response = self.client.post(
            reverse("freedom_plan_draft", args=(draft.plan_id,)),
            _payload(desired_monthly_lifestyle="25000000", total_target="1"),
        )
        self.assertEqual(response.status_code, 302)
        draft.refresh_from_db()
        self.assertEqual(draft.desired_monthly_lifestyle, 25000000)
        self.assertGreater(draft.total_target, 1)

        response = self.client.post(
            reverse("freedom_plan_save", args=(draft.plan_id,)),
            _payload(desired_monthly_lifestyle="30000000", total_target="1"),
        )
        self.assertEqual(response.status_code, 302)
        draft.refresh_from_db()
        self.assertEqual(draft.status, FreedomScenario.Status.SAVED)
        self.assertEqual(draft.version, 1)
        self.assertGreater(draft.total_target, 1)
        self.assertRedirects(response, reverse("freedom_scenario_detail", args=(draft.plan_id, draft.pk)))

    def test_invalid_form_preserves_input(self) -> None:
        draft = self._create_draft()
        response = self.client.post(
            reverse("freedom_plan_draft", args=(draft.plan_id,)),
            _payload(target_date="2026-09-01", desired_monthly_lifestyle="12345678"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Target date must be after")
        self.assertContains(response, "12345678")

    def test_foreign_objects_are_404_and_mutations_are_post_only(self) -> None:
        draft = self._create_draft()
        plan_id = draft.plan_id
        self.client.login(username="other", password="pass")
        for url in (
            reverse("freedom_plan_draft", args=(plan_id,)),
            reverse("freedom_plan_save", args=(plan_id,)),
            reverse("freedom_plan_archive", args=(plan_id,)),
        ):
            self.assertEqual(self.client.post(url).status_code, 404)
        detail_url = reverse("freedom_scenario_detail", args=(plan_id, draft.pk))
        self.assertEqual(self.client.get(detail_url).status_code, 404)

        self.client.login(username="planner", password="pass")
        for url in (
            reverse("freedom_plan_save", args=(plan_id,)),
            reverse("freedom_plan_archive", args=(plan_id,)),
            reverse("freedom_plan_restore", args=(plan_id,)),
            reverse("freedom_plan_rename", args=(plan_id,)),
        ):
            self.assertEqual(self.client.get(url).status_code, 405)

    def test_update_duplicate_rename_archive_and_restore(self) -> None:
        saved = self._create_draft()
        self.client.post(reverse("freedom_plan_save", args=(saved.plan_id,)))
        saved.refresh_from_db()
        original_target = saved.total_target

        response = self.client.post(reverse("freedom_scenario_update", args=(saved.plan_id, saved.pk)))
        self.assertEqual(response.status_code, 302)
        clone = FreedomScenario.objects.get(plan_id=saved.plan_id, status=FreedomScenario.Status.DRAFT)
        self.assertEqual(clone.based_on, saved)
        saved.refresh_from_db()
        self.assertEqual(saved.total_target, original_target)

        response = self.client.post(reverse("freedom_scenario_duplicate", args=(saved.plan_id, saved.pk)))
        duplicate = FreedomScenario.objects.exclude(plan_id=saved.plan_id).get()
        self.assertRedirects(response, reverse("freedom_plan_draft", args=(duplicate.plan_id,)))

        self.client.post(reverse("freedom_plan_rename", args=(saved.plan_id,)), {"name": "Renamed"})
        saved.plan.refresh_from_db()
        self.assertEqual(saved.plan.name, "Renamed")
        self.client.post(reverse("freedom_plan_archive", args=(saved.plan_id,)))
        saved.plan.refresh_from_db()
        self.assertTrue(saved.plan.is_archived)
        self.client.post(reverse("freedom_plan_restore", args=(saved.plan_id,)))
        saved.plan.refresh_from_db()
        self.assertFalse(saved.plan.is_archived)

    def test_csrf_is_enforced_for_save(self) -> None:
        draft = self._create_draft()
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.login(username="planner", password="pass")
        response = csrf_client.post(reverse("freedom_plan_save", args=(draft.plan_id,)))
        self.assertEqual(response.status_code, 403)
