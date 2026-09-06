from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase

from prospectus_lumos.apps.financial_planning.models import FinancialEvent, FreedomPlan, FreedomScenario
from prospectus_lumos.apps.financial_planning.services import (
    DraftAlreadyExistsError,
    FreedomScenarioService,
    ImmutableScenarioError,
)


def _inputs(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "calculation_date": date(2026, 9, 4),
        "target_date": date(2046, 9, 1),
        "desired_monthly_lifestyle": Decimal("20000000"),
        "current_investable_assets": Decimal("100000000"),
        "current_monthly_investment": Decimal("10000000"),
        "current_monthly_income": Decimal("30000000"),
        "current_monthly_expenses": Decimal("15000000"),
        "emergency_savings": Decimal("50000000"),
        "post_freedom_monthly_income": Decimal("0"),
        "withdrawal_rate": Decimal("4"),
        "annual_return_rate": Decimal("7"),
        "annual_inflation_rate": Decimal("3"),
        "annual_income_growth_rate": Decimal("0"),
        "annual_contribution_growth_rate": Decimal("0"),
        "safety_buffer_rate": Decimal("10"),
        "include_emergency_reserve_in_target": False,
    }
    values.update(overrides)
    return values


class FreedomScenarioServiceTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(username="owner", password="pass")
        self.other_user = User.objects.create_user(username="other", password="pass")
        self.service = FreedomScenarioService()

    def _draft(self, **overrides: object) -> FreedomScenario:
        return self.service.create_plan_with_draft(
            user=self.user,
            plan_data={"name": "Freedom at 45"},
            scenario_data=_inputs(**overrides),
            events=(
                {
                    "name": "Car",
                    "category": FinancialEvent.Category.CAR,
                    "event_date": date(2030, 1, 1),
                    "one_time_amount": Decimal("300000000"),
                    "funding_source": FinancialEvent.FundingSource.INVESTMENT_PORTFOLIO,
                    "sort_order": 0,
                },
            ),
        )

    def test_create_update_and_one_draft_behavior(self) -> None:
        draft = self._draft()
        original_target = draft.total_target
        updated = self.service.update_draft(
            user=self.user,
            draft=draft,
            scenario_data=_inputs(desired_monthly_lifestyle=Decimal("25000000")),
        )
        self.assertGreater(updated.total_target, original_target)
        with self.assertRaises(DraftAlreadyExistsError):
            self.service._create_draft(plan=draft.plan, scenario_data=_inputs(), events=())

    def test_save_recalculates_authoritatively_and_allocates_versions(self) -> None:
        draft = self._draft()
        draft.total_target = Decimal("1")
        draft.projection_data = {"forged": True}
        draft.save(update_fields=("total_target", "projection_data"))
        saved = self.service.save_draft(user=self.user, draft=draft)
        self.assertEqual(saved.version, 1)
        self.assertEqual(saved.status, FreedomScenario.Status.SAVED)
        self.assertGreater(saved.total_target, Decimal("1"))
        self.assertNotIn("forged", saved.projection_data)
        self.assertEqual(saved.projection_data["schema_version"], 1)

        second_draft = self.service.clone_to_draft(user=self.user, scenario=saved)
        second = self.service.save_draft(user=self.user, draft=second_draft)
        self.assertEqual(second.version, 2)

    def test_saved_scenario_is_immutable_through_service(self) -> None:
        saved = self.service.save_draft(user=self.user, draft=self._draft())
        with self.assertRaises(ImmutableScenarioError):
            self.service.update_draft(user=self.user, draft=saved, scenario_data=_inputs())
        saved.refresh_from_db()
        self.assertEqual(saved.version, 1)

    def test_ownership_is_enforced(self) -> None:
        draft = self._draft()
        with self.assertRaises(PermissionDenied):
            self.service.update_draft(user=self.other_user, draft=draft, scenario_data=_inputs())
        with self.assertRaises(PermissionDenied):
            self.service.set_archived(user=self.other_user, plan=draft.plan, archived=True)

    def test_clone_and_duplicate_copy_inputs_and_events_without_outputs(self) -> None:
        saved = self.service.save_draft(user=self.user, draft=self._draft())
        clone = self.service.clone_to_draft(user=self.user, scenario=saved)
        self.assertEqual(clone.based_on, saved)
        self.assertEqual(clone.version, 0)
        self.assertEqual(clone.events.get().name, "Car")
        self.assertNotEqual(clone.pk, saved.pk)
        self.service.save_draft(user=self.user, draft=clone)

        duplicate = self.service.duplicate_to_new_plan(user=self.user, scenario=saved)
        self.assertNotEqual(duplicate.plan_id, saved.plan_id)
        self.assertEqual(duplicate.current_investable_assets, saved.current_investable_assets)
        self.assertEqual(duplicate.events.count(), 1)
        self.assertEqual(duplicate.status, FreedomScenario.Status.DRAFT)

    def test_atomic_creation_rolls_back_plan_when_event_is_invalid(self) -> None:
        with self.assertRaises(ValidationError):
            self.service.create_plan_with_draft(
                user=self.user,
                plan_data={"name": "Rollback"},
                scenario_data=_inputs(),
                events=({"name": "Invalid", "event_date": date(2030, 1, 1), "one_time_amount": 0},),
            )
        self.assertFalse(FreedomPlan.objects.filter(name="Rollback").exists())

    def test_calculation_failure_rolls_back_save_transition(self) -> None:
        draft = self._draft()
        with patch.object(self.service.calculator, "calculate", side_effect=RuntimeError("calculation failed")):
            with self.assertRaises(RuntimeError):
                self.service.save_draft(user=self.user, draft=draft)
        draft.refresh_from_db()
        self.assertEqual(draft.status, FreedomScenario.Status.DRAFT)
        self.assertEqual(draft.version, 0)
