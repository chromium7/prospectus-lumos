from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from prospectus_lumos.apps.financial_planning.models import FinancialEvent, FreedomScenario
from prospectus_lumos.apps.financial_planning.services import FreedomScenarioService
from prospectus_lumos.website.financial_planning.comparison import compare_events, compare_scenarios


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


class ScenarioComparisonTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(username="comparer", password="pass")
        self.other = User.objects.create_user(username="outsider", password="pass")
        self.service = FreedomScenarioService()
        draft = self.service.create_plan_with_draft(
            user=self.user,
            plan_data={"name": "Two ways", "description": ""},
            scenario_data=_scenario_values(),
        )
        self.plan = draft.plan
        self.first = self.service.save_draft(user=self.user, draft=draft)

    def _next_version(self, **scenario_data: object) -> FreedomScenario:
        latest = self.plan.scenarios.filter(status=FreedomScenario.Status.SAVED).order_by("-version").first()
        assert latest is not None
        draft = self.service.clone_to_draft(user=self.user, scenario=latest)
        if scenario_data:
            draft = self.service.update_draft(user=self.user, draft=draft, scenario_data=scenario_data)
        return self.service.save_draft(user=self.user, draft=draft)

    def test_investing_more_makes_the_date_favourable(self) -> None:
        second = self._next_version(current_monthly_investment=Decimal("25000000"))
        comparison = compare_scenarios(self.first, second)
        date_row = next(
            row for group in comparison["groups"] for row in group["rows"] if row.label == "Date at your current pace"
        )
        self.assertTrue(date_row.changed)
        self.assertEqual(date_row.direction, "better")
        self.assertIn("earlier", date_row.note)

    def test_wanting_more_makes_the_date_unfavourable(self) -> None:
        second = self._next_version(desired_monthly_lifestyle=Decimal("40000000"))
        comparison = compare_scenarios(self.first, second)
        date_row = next(
            row for group in comparison["groups"] for row in group["rows"] if row.label == "Date at your current pace"
        )
        required_row = next(
            row for group in comparison["groups"] for row in group["rows"] if row.label == "Suggested each month"
        )
        self.assertEqual(date_row.direction, "worse")
        self.assertEqual(required_row.direction, "worse")

    def test_unchanged_answers_are_not_flagged(self) -> None:
        second = self._next_version()
        comparison = compare_scenarios(self.first, second)
        lifestyle_row = next(
            row for group in comparison["groups"] for row in group["rows"] if row.label == "Monthly lifestyle you want"
        )
        self.assertFalse(lifestyle_row.changed)
        self.assertFalse(comparison["event_changes"])

    def test_event_differences_are_detected(self) -> None:
        latest = self.plan.scenarios.filter(status=FreedomScenario.Status.SAVED).order_by("-version").first()
        assert latest is not None
        draft = self.service.clone_to_draft(user=self.user, scenario=latest)
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
        second = self.service.save_draft(user=self.user, draft=draft)

        events = compare_events(self.first, second)
        self.assertEqual([event["name"] for event in events["added"]], ["Buy a family car"])
        self.assertEqual(events["removed"], [])

        third_draft = self.service.clone_to_draft(user=self.user, scenario=second)
        self.service.update_draft(
            user=self.user,
            draft=third_draft,
            scenario_data={},
            events=[
                {
                    "name": "Buy a family car",
                    "category": FinancialEvent.Category.CAR,
                    "event_date": date(year + 3, 1, 1),
                    "one_time_amount": Decimal("450000000"),
                    "funding_source": FinancialEvent.FundingSource.SEPARATE_SAVINGS,
                    "sort_order": 0,
                }
            ],
        )
        third = self.service.save_draft(user=self.user, draft=third_draft)
        changed = compare_events(second, third)["changed"]
        self.assertEqual(len(changed), 1)
        self.assertEqual(changed[0]["before"]["one_time_amount"], Decimal("300000000.00"))
        self.assertEqual(changed[0]["after"]["one_time_amount"], Decimal("450000000.00"))

    def test_same_named_events_on_the_same_date_remain_distinct(self) -> None:
        latest = self.plan.scenarios.filter(status=FreedomScenario.Status.SAVED).order_by("-version").first()
        assert latest is not None
        event_date = date(timezone.localdate().year + 3, 1, 1)
        draft = self.service.clone_to_draft(user=self.user, scenario=latest)
        self.service.update_draft(
            user=self.user,
            draft=draft,
            scenario_data={},
            events=[
                {
                    "name": "Education",
                    "category": FinancialEvent.Category.EDUCATION,
                    "event_date": event_date,
                    "one_time_amount": Decimal("100000000"),
                    "funding_source": FinancialEvent.FundingSource.INVESTMENT_PORTFOLIO,
                    "sort_order": 0,
                },
                {
                    "name": "Education",
                    "category": FinancialEvent.Category.EDUCATION,
                    "event_date": event_date,
                    "one_time_amount": Decimal("200000000"),
                    "funding_source": FinancialEvent.FundingSource.INVESTMENT_PORTFOLIO,
                    "sort_order": 1,
                },
            ],
        )
        version_with_two_events = self.service.save_draft(user=self.user, draft=draft)

        next_draft = self.service.clone_to_draft(user=self.user, scenario=version_with_two_events)
        self.service.update_draft(
            user=self.user,
            draft=next_draft,
            scenario_data={},
            events=[
                {
                    "name": "Education",
                    "category": FinancialEvent.Category.EDUCATION,
                    "event_date": event_date,
                    "one_time_amount": Decimal("100000000"),
                    "funding_source": FinancialEvent.FundingSource.INVESTMENT_PORTFOLIO,
                    "sort_order": 0,
                }
            ],
        )
        version_with_one_event = self.service.save_draft(user=self.user, draft=next_draft)

        events = compare_events(version_with_two_events, version_with_one_event)
        self.assertEqual(len(events["unchanged"]), 1)
        self.assertEqual(len(events["removed"]), 1)
        self.assertEqual(events["removed"][0]["one_time_amount"], Decimal("200000000.00"))

    def test_comparison_leaves_both_versions_untouched(self) -> None:
        second = self._next_version(current_monthly_investment=Decimal("25000000"))
        self.first.refresh_from_db()
        second.refresh_from_db()
        before = (self.first.total_target, second.total_target)
        compare_scenarios(self.first, second)
        self.first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual((self.first.total_target, second.total_target), before)

    def test_page_renders_both_versions_and_links_back(self) -> None:
        second = self._next_version(current_monthly_investment=Decimal("25000000"))
        self.client.login(username="comparer", password="pass")
        response = self.client.get(
            reverse("freedom_scenario_compare", args=(self.plan.pk,)),
            {"left": self.first.pk, "right": second.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("freedom_scenario_detail", args=(self.plan.pk, self.first.pk)))
        self.assertContains(response, reverse("freedom_scenario_detail", args=(self.plan.pk, second.pk)))
        self.assertContains(response, "Easier")

    def test_order_is_normalised_to_older_then_newer(self) -> None:
        second = self._next_version()
        self.client.login(username="comparer", password="pass")
        response = self.client.get(
            reverse("freedom_scenario_compare", args=(self.plan.pk,)),
            {"left": second.pk, "right": self.first.pk},
        )
        comparison = response.context["comparison"]
        self.assertEqual(comparison["left"].pk, self.first.pk)
        self.assertEqual(comparison["right"].pk, second.pk)

    def test_the_same_version_twice_is_rejected(self) -> None:
        self._next_version()
        self.client.login(username="comparer", password="pass")
        response = self.client.get(
            reverse("freedom_scenario_compare", args=(self.plan.pk,)),
            {"left": self.first.pk, "right": self.first.pk},
        )
        self.assertIsNone(response.context["comparison"])
        self.assertContains(response, "Choose two different versions.")

    def test_a_scenario_from_another_plan_is_rejected(self) -> None:
        second = self._next_version()
        other_draft = self.service.create_plan_with_draft(
            user=self.user,
            plan_data={"name": "Different plan", "description": ""},
            scenario_data=_scenario_values(),
        )
        foreign = self.service.save_draft(user=self.user, draft=other_draft)
        self.client.login(username="comparer", password="pass")
        response = self.client.get(
            reverse("freedom_scenario_compare", args=(self.plan.pk,)),
            {"left": second.pk, "right": foreign.pk},
        )
        self.assertIsNone(response.context["comparison"])

    def test_another_users_plan_is_not_found(self) -> None:
        self.client.login(username="outsider", password="pass")
        response = self.client.get(reverse("freedom_scenario_compare", args=(self.plan.pk,)))
        self.assertEqual(response.status_code, 404)

    def test_comparison_requires_authentication(self) -> None:
        response = self.client.get(reverse("freedom_scenario_compare", args=(self.plan.pk,)))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response["Location"])

    def test_a_single_version_explains_what_to_do_next(self) -> None:
        self.client.login(username="comparer", password="pass")
        response = self.client.get(reverse("freedom_scenario_compare", args=(self.plan.pk,)))
        self.assertContains(response, "You need two saved versions first")
