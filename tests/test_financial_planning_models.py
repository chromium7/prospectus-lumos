from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.test import TestCase

from prospectus_lumos.apps.financial_planning.models import FinancialEvent, FreedomPlan, FreedomScenario


def _scenario(plan: FreedomPlan, **overrides: object) -> FreedomScenario:
    values: dict[str, object] = {
        "plan": plan,
        "version": 0,
        "status": FreedomScenario.Status.DRAFT,
        "calculation_date": date(2026, 9, 1),
        "target_date": date(2046, 9, 1),
        "desired_monthly_lifestyle": Decimal("20000000"),
    }
    values.update(overrides)
    return FreedomScenario.objects.create(**values)


class FinancialPlanningModelTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(username="planner", password="pass")
        self.plan = FreedomPlan.objects.create(user=self.user, name="Freedom at 45")

    def test_plan_latest_saved_is_derived_by_version(self) -> None:
        _scenario(self.plan, version=1, status=FreedomScenario.Status.SAVED)
        latest = _scenario(self.plan, version=2, status=FreedomScenario.Status.SAVED)
        self.assertEqual(self.plan.latest_saved_scenario, latest)

    def test_constraints_enforce_version_and_one_draft(self) -> None:
        _scenario(self.plan)
        with self.assertRaises(IntegrityError), transaction.atomic():
            _scenario(self.plan)

        other_plan = FreedomPlan.objects.create(user=self.user, name="Other")
        with self.assertRaises(IntegrityError), transaction.atomic():
            _scenario(other_plan, version=0, status=FreedomScenario.Status.SAVED)

    def test_constraints_reject_invalid_scenario_and_event_values(self) -> None:
        with self.assertRaises(IntegrityError), transaction.atomic():
            _scenario(self.plan, target_date=date(2026, 9, 1))

        draft = _scenario(self.plan)
        with self.assertRaises(IntegrityError), transaction.atomic():
            FinancialEvent.objects.create(
                scenario=draft,
                name="Empty event",
                event_date=date(2030, 1, 1),
                one_time_amount=0,
                recurring_monthly_amount=0,
            )

    def test_projection_json_round_trip_preserves_money_strings(self) -> None:
        scenario = _scenario(self.plan)
        scenario.projection_data = {"schema_version": 1, "target": "123456789.12", "warnings": []}
        scenario.save(update_fields=("projection_data",))
        scenario.refresh_from_db()
        self.assertEqual(scenario.projection_data["target"], "123456789.12")
