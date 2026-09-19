from __future__ import annotations

import time
from datetime import date
from pathlib import Path
from decimal import Decimal

from django.contrib.auth.models import User
from django.contrib.staticfiles import finders
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from prospectus_lumos.apps.financial_planning.models import FinancialEvent, FreedomScenario
from prospectus_lumos.apps.financial_planning.services import FreedomScenarioService

GET_ROUTES = (
    ("freedom_plan_list", ()),
    ("freedom_plan_create", ()),
    ("freedom_plan_goal", ("plan",)),
    ("freedom_plan_money", ("plan",)),
    ("freedom_plan_events", ("plan",)),
    ("freedom_plan_review", ("plan",)),
    ("freedom_plan_draft", ("plan",)),
    ("freedom_scenario_compare", ("plan",)),
    ("freedom_scenario_detail", ("plan", "scenario")),
)

POST_ROUTES = (
    ("freedom_plan_save", ("plan",)),
    ("freedom_plan_rename", ("plan",)),
    ("freedom_plan_archive", ("plan",)),
    ("freedom_plan_restore", ("plan",)),
    ("freedom_scenario_update", ("plan", "scenario")),
    ("freedom_scenario_duplicate", ("plan", "scenario")),
)


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


class PlannerRouteAuditTests(TestCase):
    """Every planner route is checked for authentication, ownership, and method limits."""

    def setUp(self) -> None:
        self.user = User.objects.create_user(username="owner", password="pass")
        self.intruder = User.objects.create_user(username="intruder", password="pass")
        self.service = FreedomScenarioService()
        draft = self.service.create_plan_with_draft(
            user=self.user,
            plan_data={"name": "Audited plan", "description": ""},
            scenario_data=_scenario_values(),
        )
        self.plan = draft.plan
        self.scenario = self.service.save_draft(user=self.user, draft=draft)
        self.service.clone_to_draft(user=self.user, scenario=self.scenario)

    def _args(self, names: tuple[str, ...]) -> tuple[int, ...]:
        lookup = {"plan": self.plan.pk, "scenario": self.scenario.pk}
        return tuple(lookup[name] for name in names)

    def test_every_route_requires_authentication(self) -> None:
        for name, arg_names in GET_ROUTES + POST_ROUTES + (("freedom_calculate_preview", ()),):
            with self.subTest(route=name):
                url = reverse(name, args=self._args(arg_names))
                response = self.client.post(url) if name not in dict(GET_ROUTES) else self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertIn("/login", response["Location"])

    def test_another_user_never_reaches_an_owned_object(self) -> None:
        self.client.login(username="intruder", password="pass")
        for name, arg_names in GET_ROUTES:
            if not arg_names:
                continue
            with self.subTest(route=name):
                response = self.client.get(reverse(name, args=self._args(arg_names)))
                self.assertEqual(response.status_code, 404)
        for name, arg_names in POST_ROUTES:
            with self.subTest(route=name):
                response = self.client.post(reverse(name, args=self._args(arg_names)), {"name": "Stolen"})
                self.assertEqual(response.status_code, 404)

    def test_another_user_cannot_preview_an_owned_draft(self) -> None:
        self.client.login(username="intruder", password="pass")
        response = self.client.post(
            reverse("freedom_calculate_preview"),
            {
                "plan_id": self.plan.pk,
                "withdrawal_rate": "4",
                "annual_return_rate": "7",
                "annual_inflation_rate": "3",
                "annual_income_growth_rate": "0",
                "annual_contribution_growth_rate": "0",
                "safety_buffer_rate": "10",
            },
        )
        self.assertEqual(response.status_code, 404)

    def test_mutations_reject_get(self) -> None:
        self.client.login(username="owner", password="pass")
        for name, arg_names in POST_ROUTES + (("freedom_calculate_preview", ()),):
            with self.subTest(route=name):
                response = self.client.get(reverse(name, args=self._args(arg_names)))
                self.assertEqual(response.status_code, 405)

    def test_mutations_require_a_csrf_token(self) -> None:
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.login(username="owner", password="pass")
        for name, arg_names in POST_ROUTES + (("freedom_calculate_preview", ()),):
            with self.subTest(route=name):
                response = csrf_client.post(reverse(name, args=self._args(arg_names)))
                self.assertEqual(response.status_code, 403)

    def test_a_saved_scenario_cannot_be_reached_through_another_plan(self) -> None:
        other_draft = self.service.create_plan_with_draft(
            user=self.user,
            plan_data={"name": "Other plan", "description": ""},
            scenario_data=_scenario_values(),
        )
        self.client.login(username="owner", password="pass")
        response = self.client.get(reverse("freedom_scenario_detail", args=(other_draft.plan_id, self.scenario.pk)))
        self.assertEqual(response.status_code, 404)

    def test_result_page_query_count_is_bounded(self) -> None:
        self.client.login(username="owner", password="pass")
        url = reverse("freedom_scenario_detail", args=(self.plan.pk, self.scenario.pk))
        self.client.get(url)
        with self.assertNumQueries(6):
            self.client.get(url)


class PlannerEdgeCaseTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(username="edge", password="pass")
        self.service = FreedomScenarioService()
        self.client.login(username="edge", password="pass")

    def _saved(self, **overrides: object) -> FreedomScenario:
        draft = self.service.create_plan_with_draft(
            user=self.user,
            plan_data={"name": f"Edge {timezone.now().timestamp()}", "description": ""},
            scenario_data=_scenario_values(**overrides),
        )
        return self.service.save_draft(user=self.user, draft=draft)

    def _assert_page_renders(self, scenario: FreedomScenario) -> str:
        response = self.client.get(reverse("freedom_scenario_detail", args=(scenario.plan_id, scenario.pk)))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_spending_every_rupiah_earned_still_produces_a_page(self) -> None:
        scenario = self._saved(
            current_monthly_expenses=Decimal("30000000"),
            current_monthly_investment=Decimal("0"),
            current_investable_assets=Decimal("0"),
        )
        body = self._assert_page_renders(scenario)
        self.assertIn("Where the target comes from", body)

    def test_an_already_funded_plan_reads_as_complete(self) -> None:
        scenario = self._saved(current_investable_assets=Decimal("90000000000"))
        self.assertEqual(scenario.result_status, FreedomScenario.ResultStatus.COMPLETE)
        self.assertIn("Already there", self._assert_page_renders(scenario))

    def test_an_unreachable_plan_explains_itself_instead_of_inventing_a_number(self) -> None:
        today = timezone.localdate()
        draft = self.service.create_plan_with_draft(
            user=self.user,
            plan_data={"name": "Unreachable", "description": ""},
            scenario_data=_scenario_values(
                target_date=date(today.year + 1, today.month, 1),
                desired_monthly_lifestyle=Decimal("500000000000"),
                annual_return_rate=Decimal("-50"),
            ),
        )
        scenario = self.service.save_draft(user=self.user, draft=draft)
        self.assertIsNone(scenario.required_monthly_investment)
        body = self._assert_page_renders(scenario)
        self.assertIn("Out of range", body)

    def test_a_hundred_year_horizon_renders(self) -> None:
        today = timezone.localdate()
        scenario = self._saved(target_date=date(today.year + 100, 1, 1))
        body = self._assert_page_renders(scenario)
        self.assertIn("Year by year", body)

    def test_very_large_amounts_keep_idr_grouping(self) -> None:
        scenario = self._saved(desired_monthly_lifestyle=Decimal("900000000000"))
        self.assertIn("Rp", self._assert_page_renders(scenario))

    def test_fifty_life_events_are_accepted_and_shown(self) -> None:
        draft = self.service.create_plan_with_draft(
            user=self.user,
            plan_data={"name": "Busy life", "description": ""},
            scenario_data=_scenario_values(),
        )
        year = timezone.localdate().year
        events = [
            {
                "name": f"Plan {index}",
                "category": FinancialEvent.Category.CUSTOM,
                "event_date": date(year + 1 + index % 15, (index % 12) + 1, 1),
                "one_time_amount": Decimal("10000000"),
                "funding_source": FinancialEvent.FundingSource.INVESTMENT_PORTFOLIO,
                "sort_order": index,
            }
            for index in range(50)
        ]
        draft = self.service.update_draft(user=self.user, draft=draft, scenario_data={}, events=events)
        scenario = self.service.save_draft(user=self.user, draft=draft)
        self.assertEqual(scenario.events.count(), 50)
        self.assertIn("Plans along the way", self._assert_page_renders(scenario))

    def test_saved_results_survive_later_source_changes(self) -> None:
        scenario = self._saved()
        scenario.refresh_from_db()
        before = (scenario.total_target, scenario.required_monthly_investment, scenario.projection_data)
        draft = self.service.clone_to_draft(user=self.user, scenario=scenario)
        self.service.update_draft(
            user=self.user,
            draft=draft,
            scenario_data={"desired_monthly_lifestyle": Decimal("99000000")},
        )
        scenario.refresh_from_db()
        self.assertEqual(
            (scenario.total_target, scenario.required_monthly_investment, scenario.projection_data), before
        )


class PreviewPerformanceTests(TestCase):
    def test_a_sixty_year_ten_event_preview_stays_responsive(self) -> None:
        user = User.objects.create_user(username="bench", password="pass")
        service = FreedomScenarioService()
        today = timezone.localdate()
        events = [
            {
                "name": f"Plan {index}",
                "event_date": date(today.year + 2 + index * 2, 1, 1),
                "one_time_amount": Decimal("50000000"),
                "recurring_monthly_amount": Decimal("0"),
                "recurring_end_date": None,
                "amount_basis": FinancialEvent.AmountBasis.TODAY,
                "funding_source": FinancialEvent.FundingSource.INVESTMENT_PORTFOLIO,
            }
            for index in range(10)
        ]
        scenario_data = _scenario_values(
            target_date=date(today.year + 60, 1, 1),
            withdrawal_rate=Decimal("4"),
            annual_return_rate=Decimal("7"),
            annual_inflation_rate=Decimal("3"),
            annual_income_growth_rate=Decimal("0"),
            annual_contribution_growth_rate=Decimal("0"),
            safety_buffer_rate=Decimal("10"),
            include_emergency_reserve_in_target=False,
            birth_date=None,
        )
        started = time.perf_counter()
        result = service.calculate_preview(scenario_data=scenario_data, events=events)
        elapsed = time.perf_counter() - started
        self.assertIsNotNone(result)
        self.assertLess(elapsed, 2.0, f"preview took {elapsed:.2f}s")
        self.assertEqual(user.freedom_plans.count(), 0)


class PlannerPresentationTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(username="presenter", password="pass")
        self.service = FreedomScenarioService()
        self.client.login(username="presenter", password="pass")
        draft = self.service.create_plan_with_draft(
            user=self.user,
            plan_data={"name": "Copy check", "description": ""},
            scenario_data=_scenario_values(),
        )
        self.plan = draft.plan
        self.scenario = self.service.save_draft(user=self.user, draft=draft)

    def test_result_and_review_pages_carry_the_non_advice_disclaimer(self) -> None:
        self.service.clone_to_draft(user=self.user, scenario=self.scenario)
        for url in (
            reverse("freedom_scenario_detail", args=(self.plan.pk, self.scenario.pk)),
            reverse("freedom_plan_review", args=(self.plan.pk,)),
        ):
            with self.subTest(url=url):
                self.assertContains(self.client.get(url), "not personalized financial advice")

    def test_result_page_uses_estimate_language_not_promises(self) -> None:
        response = self.client.get(reverse("freedom_scenario_detail", args=(self.plan.pk, self.scenario.pk)))
        body = response.content.decode().lower()
        self.assertIn("estimate", body)
        for promise in ("guarantee", "you will have", "risk-free", "guaranteed"):
            self.assertNotIn(promise, body)

    def test_dark_mode_styles_cover_the_surfaces_the_planner_uses(self) -> None:
        path = finders.find("css/theme.css")
        assert path is not None
        stylesheet = Path(path).read_text()
        for selector in (".dark .list-group-item", ".dark .table caption", ".dark details > summary"):
            self.assertIn(selector, stylesheet)

    def test_tables_stay_scrollable_on_small_screens(self) -> None:
        response = self.client.get(reverse("freedom_scenario_detail", args=(self.plan.pk, self.scenario.pk)))
        body = response.content.decode()
        self.assertEqual(body.count('class="table-responsive'), 3)
        self.assertIn('class="table-responsive result-milestones"', body)

    def test_chart_has_a_text_alternative(self) -> None:
        response = self.client.get(reverse("freedom_scenario_detail", args=(self.plan.pk, self.scenario.pk)))
        body = response.content.decode()
        self.assertIn('role="img"', body)
        self.assertIn('aria-label="Line chart of estimated investments', body)
