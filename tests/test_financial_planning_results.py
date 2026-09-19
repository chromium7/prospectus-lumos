from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from prospectus_lumos.apps.financial_planning.models import FinancialEvent, FreedomPlan, FreedomScenario
from prospectus_lumos.apps.financial_planning.services import FreedomScenarioService
from prospectus_lumos.website.financial_planning.results import scenario_result_context


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


class ScenarioResultPageTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(username="reader", password="pass")
        self.other = User.objects.create_user(username="stranger", password="pass")
        self.service = FreedomScenarioService()
        self.draft = self.service.create_plan_with_draft(
            user=self.user,
            plan_data={"name": "Freedom at 45", "description": "Calmer years"},
            scenario_data=_scenario_values(),
        )
        self.plan = self.draft.plan

    def _save_version(self) -> FreedomScenario:
        draft = self.plan.scenarios.get(status=FreedomScenario.Status.DRAFT)
        return self.service.save_draft(user=self.user, draft=draft)

    def _get_detail(self, scenario: FreedomScenario) -> object:
        self.client.login(username="reader", password="pass")
        return self.client.get(reverse("freedom_scenario_detail", args=(self.plan.pk, scenario.pk)))

    def test_context_is_built_from_stored_outputs_only(self) -> None:
        scenario = self._save_version()
        scenario.refresh_from_db()
        stored_target = scenario.total_target

        # A later edit to the plan's live draft must not change what the saved version reports.
        self.service.clone_to_draft(user=self.user, scenario=scenario)
        draft = self.plan.scenarios.get(status=FreedomScenario.Status.DRAFT)
        self.service.update_draft(
            user=self.user,
            draft=draft,
            scenario_data={"desired_monthly_lifestyle": Decimal("80000000")},
        )

        scenario.refresh_from_db()
        context = scenario_result_context(scenario)
        self.assertEqual(scenario.total_target, stored_target)
        self.assertEqual(
            context["target_breakdown"][-1]["amount"] + Decimal("0"),
            Decimal(scenario.projection_data["target_breakdown"]["safety_buffer"]),
        )

    def test_scenario_range_reports_three_stored_cases(self) -> None:
        scenario = self._save_version()
        cases = scenario_result_context(scenario)["cases"]
        self.assertEqual([case["name"] for case in cases], ["conservative", "base", "optimistic"])
        stored = scenario.projection_data["cases"]
        for case in cases:
            self.assertEqual(case["annual_return_rate"], Decimal(stored[case["name"]]["annual_return_rate"]))
            self.assertEqual(case["total_target"], Decimal(stored[case["name"]]["target_breakdown"]["total_target"]))
        self.assertLess(cases[0]["annual_return_rate"], cases[2]["annual_return_rate"])

    def test_range_makes_no_probability_claim(self) -> None:
        response = self._get_detail(self._save_version())
        body = response.content.decode()
        for forbidden in ("probability", "likely", "chance of", "% confidence"):
            self.assertNotIn(forbidden, body.lower())

    def test_annual_rows_stop_at_the_target_year(self) -> None:
        scenario = self._save_version()
        rows = scenario_result_context(scenario)["annual_rows"]
        self.assertTrue(rows)
        self.assertLessEqual(rows[-1]["year"], scenario.target_date.year)
        self.assertEqual(rows[-1]["year"], scenario.target_date.year)

    def test_events_describe_funding_without_jargon(self) -> None:
        draft = self.plan.scenarios.get(status=FreedomScenario.Status.DRAFT)
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
                },
                {
                    "name": "Home deposit",
                    "category": FinancialEvent.Category.HOME,
                    "event_date": date(year + 4, 1, 1),
                    "one_time_amount": Decimal("500000000"),
                    "funding_source": FinancialEvent.FundingSource.SEPARATE_SAVINGS,
                    "sort_order": 1,
                },
            ],
        )
        scenario = self._save_version()

        rows = scenario_result_context(scenario)["event_rows"]
        self.assertEqual([row["event"].name for row in rows], ["Buy a family car", "Home deposit"])
        self.assertTrue(rows[0]["from_portfolio"])
        self.assertFalse(rows[1]["from_portfolio"])
        self.assertIsNone(rows[0]["separate_need"])
        self.assertIsNotNone(rows[1]["separate_need"])

    def test_page_answers_before_it_explains(self) -> None:
        response = self._get_detail(self._save_version())
        body = response.content.decode()
        self.assertEqual(response.status_code, 200)
        answer = body.index("To spend")
        cards = body.index("What you need in total")
        assumptions = body.index("How this was estimated")
        self.assertLess(answer, cards)
        self.assertLess(cards, assumptions)

    def test_page_is_understandable_without_a_chart(self) -> None:
        response = self._get_detail(self._save_version())
        body = response.content.decode()
        for heading in ("Where the target comes from", "If things go differently", "Year by year"):
            self.assertIn(heading, body)

    def test_version_history_lists_every_saved_version(self) -> None:
        first = self._save_version()
        self.service.clone_to_draft(user=self.user, scenario=first)
        second = self._save_version()

        response = self._get_detail(second)
        self.assertContains(response, "Version 1")
        self.assertContains(response, "Version 2")
        self.assertContains(response, reverse("freedom_scenario_detail", args=(self.plan.pk, first.pk)))

    def test_another_user_cannot_open_a_saved_result(self) -> None:
        scenario = self._save_version()
        self.client.login(username="stranger", password="pass")
        response = self.client.get(reverse("freedom_scenario_detail", args=(self.plan.pk, scenario.pk)))
        self.assertEqual(response.status_code, 404)

    def test_draft_scenario_redirects_to_the_wizard(self) -> None:
        draft = self.plan.scenarios.get(status=FreedomScenario.Status.DRAFT)
        self.client.login(username="reader", password="pass")
        response = self.client.get(reverse("freedom_scenario_detail", args=(self.plan.pk, draft.pk)))
        self.assertEqual(response.status_code, 302)

    def test_empty_projection_data_still_renders(self) -> None:
        plan = FreedomPlan.objects.create(user=self.user, name="Legacy")
        scenario = FreedomScenario.objects.create(
            plan=plan,
            version=1,
            status=FreedomScenario.Status.SAVED,
            calculation_date=date(2026, 9, 4),
            target_date=date(2046, 9, 1),
            desired_monthly_lifestyle=Decimal("20000000"),
            saved_at=timezone.now(),
        )
        self.client.login(username="reader", password="pass")
        response = self.client.get(reverse("freedom_scenario_detail", args=(plan.pk, scenario.pk)))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "no stored year-by-year data")
