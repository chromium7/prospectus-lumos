from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from prospectus_lumos.apps.financial_planning.models import FinancialEvent, FreedomPlan, FreedomScenario
from prospectus_lumos.apps.financial_planning.services import FreedomScenarioService


def _scenario_values(**overrides: object) -> dict[str, object]:
    today = timezone.localdate()
    values: dict[str, object] = {
        "calculation_date": today,
        "target_date": date(today.year + 20, 1, 1),
        "desired_monthly_lifestyle": Decimal("20000000"),
        "post_freedom_monthly_income": Decimal("0"),
        "current_monthly_income": Decimal("30000000"),
        "current_monthly_expenses": Decimal("15000000"),
        "current_monthly_investment": Decimal("10000000"),
        "current_investable_assets": Decimal("100000000"),
        "emergency_savings": Decimal("50000000"),
    }
    values.update(overrides)
    return values


class PlanLibraryCardTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(username="librarian", password="pass")
        self.service = FreedomScenarioService()
        self.client.login(username="librarian", password="pass")

    def _plan_with_saved_version(self, name: str, **scenario_overrides: object) -> FreedomScenario:
        draft = self.service.create_plan_with_draft(
            user=self.user,
            plan_data={"name": name, "description": ""},
            scenario_data=_scenario_values(**scenario_overrides),
        )
        return self.service.save_draft(user=self.user, draft=draft)

    def test_card_states_a_plain_language_status(self) -> None:
        self._plan_with_saved_version("Behind plan")
        response = self.client.get(reverse("freedom_plan_list"))
        self.assertContains(response, "Needs more each month")
        self.assertContains(response, "of the target saved so far")

    def test_card_shows_an_already_funded_plan_as_complete(self) -> None:
        self._plan_with_saved_version("Funded plan", current_investable_assets=Decimal("90000000000"))
        response = self.client.get(reverse("freedom_plan_list"))
        self.assertContains(response, "Already there")

    def test_card_counts_versions_and_plans(self) -> None:
        saved = self._plan_with_saved_version("Counted plan")
        draft = self.service.clone_to_draft(user=self.user, scenario=saved)
        year = timezone.localdate().year
        self.service.update_draft(
            user=self.user,
            draft=draft,
            scenario_data={},
            events=[
                {
                    "name": "Buy a family car",
                    "category": FinancialEvent.Category.CAR,
                    "event_date": date(year + 3, 1, 1),
                    "one_time_amount": Decimal("300000000"),
                    "funding_source": FinancialEvent.FundingSource.INVESTMENT_PORTFOLIO,
                    "sort_order": 0,
                }
            ],
        )
        self.service.save_draft(user=self.user, draft=draft)

        response = self.client.get(reverse("freedom_plan_list"))
        self.assertContains(response, "2 of 2")
        self.assertContains(response, "1 plan along the way")
        self.assertContains(response, reverse("freedom_scenario_compare", args=(saved.plan_id,)))

    def test_a_single_version_offers_no_comparison_link(self) -> None:
        saved = self._plan_with_saved_version("Single version")
        response = self.client.get(reverse("freedom_plan_list"))
        self.assertNotContains(response, reverse("freedom_scenario_compare", args=(saved.plan_id,)))

    def test_a_plan_without_a_saved_version_says_so(self) -> None:
        self.service.create_plan_with_draft(
            user=self.user,
            plan_data={"name": "Just started", "description": ""},
            scenario_data=_scenario_values(),
        )
        response = self.client.get(reverse("freedom_plan_list"))
        self.assertContains(response, "Still answering")
        self.assertContains(response, "No saved version yet.")

    def test_progress_bar_is_clamped_and_labelled(self) -> None:
        self._plan_with_saved_version("Overshoot", current_investable_assets=Decimal("90000000000"))
        response = self.client.get(reverse("freedom_plan_list"))
        body = response.content.decode()
        self.assertIn('aria-valuenow="100"', body)
        self.assertIn('aria-label="Progress toward the target"', body)

    def test_library_query_count_does_not_grow_with_plans(self) -> None:
        for index in range(3):
            self._plan_with_saved_version(f"Plan {index}")
        with self.assertNumQueries(5):
            self.client.get(reverse("freedom_plan_list"))
        for index in range(3, 8):
            self._plan_with_saved_version(f"Plan {index}")
        with self.assertNumQueries(5):
            self.client.get(reverse("freedom_plan_list"))

    def test_another_users_plans_are_not_listed(self) -> None:
        stranger = User.objects.create_user(username="stranger-librarian", password="pass")
        FreedomPlan.objects.create(user=stranger, name="Private plan")
        self._plan_with_saved_version("Mine")
        response = self.client.get(reverse("freedom_plan_list"))
        self.assertNotContains(response, "Private plan")
        self.assertContains(response, "Mine")


class PlannerSidebarTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(username="navigator", password="pass")
        self.service = FreedomScenarioService()
        self.client.login(username="navigator", password="pass")
        draft = self.service.create_plan_with_draft(
            user=self.user,
            plan_data={"name": "Nav plan", "description": ""},
            scenario_data=_scenario_values(),
        )
        self.plan = draft.plan
        self.saved = self.service.save_draft(user=self.user, draft=draft)

    def test_every_planner_page_marks_the_sidebar_entry_active(self) -> None:
        urls = (
            reverse("freedom_plan_list"),
            reverse("freedom_plan_create"),
            reverse("freedom_scenario_detail", args=(self.plan.pk, self.saved.pk)),
            reverse("freedom_scenario_compare", args=(self.plan.pk,)),
        )
        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["selected_tab"], "financial_freedom")
                self.assertContains(response, '<a class="nav-link active" href="/freedom-plans/">', html=False)

    def test_other_pages_do_not_mark_the_planner_active(self) -> None:
        response = self.client.get(reverse("portfolio_analyzer"))
        self.assertNotContains(response, '<a class="nav-link active" href="/freedom-plans/">', html=False)
