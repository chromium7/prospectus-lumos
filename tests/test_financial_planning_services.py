from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase

from prospectus_lumos.apps.accounts.models import DocumentSource
from prospectus_lumos.apps.documents.models import Document
from prospectus_lumos.apps.financial_planning.models import FinancialEvent, FreedomPlan, FreedomScenario
from prospectus_lumos.apps.financial_planning.services import (
    ActualsSnapshotService,
    DraftAlreadyExistsError,
    FreedomScenarioService,
    ImmutableScenarioError,
)
from prospectus_lumos.apps.transactions.models import Transaction


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

    def test_actuals_snapshot_uses_represented_months_exclusions_and_owned_data(self) -> None:
        source = DocumentSource.objects.create(
            user=self.user,
            source_type=DocumentSource.SourceType.DIRECT_UPLOAD,
            name="Budget",
        )
        other_source = DocumentSource.objects.create(
            user=self.other_user,
            source_type=DocumentSource.SourceType.DIRECT_UPLOAD,
            name="Other budget",
        )

        def create_document(
            *,
            month: int,
            income: Decimal,
            expenses: Decimal,
            user: User = self.user,
            source_obj: DocumentSource = source,
        ) -> Document:
            return Document.objects.create(
                user=user,
                source=source_obj,
                month=month,
                year=2026,
                total_income=income,
                total_expenses=expenses,
            )

        create_document(month=1, income=Decimal("70"), expenses=Decimal("20"))
        march = create_document(month=3, income=Decimal("110"), expenses=Decimal("50"))
        april = create_document(month=4, income=Decimal("90"), expenses=Decimal("30"))
        create_document(month=9, income=Decimal("500"), expenses=Decimal("500"))
        create_document(
            month=5,
            income=Decimal("9999"),
            expenses=Decimal("1"),
            user=self.other_user,
            source_obj=other_source,
        )
        Transaction.objects.bulk_create(
            [
                Transaction(
                    document=march, transaction_type="income", amount=100, description="Salary", category="Salary"
                ),
                Transaction(
                    document=march, transaction_type="income", amount=10, description="Bonus", category="Bonus"
                ),
                Transaction(document=march, transaction_type="expense", amount=40, description="Rent", category="Rent"),
                Transaction(document=march, transaction_type="expense", amount=10, description="Food", category="Food"),
                Transaction(
                    document=april, transaction_type="income", amount=90, description="Salary", category="Salary"
                ),
                Transaction(document=april, transaction_type="expense", amount=30, description="Food", category="Food"),
            ]
        )

        snapshot = ActualsSnapshotService(user=self.user, snapshot_date=date(2026, 9, 6)).create_snapshot(
            period="3m",
            excluded_income_categories=("Bonus",),
            excluded_expense_categories=("Rent",),
        )

        self.assertEqual(snapshot.represented_months, ("2026-03", "2026-04"))
        self.assertEqual(snapshot.missing_months, ("2026-02",))
        self.assertEqual(snapshot.month_count, 2)
        self.assertEqual(snapshot.average_income, Decimal("95.00"))
        self.assertEqual(snapshot.average_expenses, Decimal("20.00"))
        self.assertEqual(snapshot.average_net_savings, Decimal("75.00"))
        self.assertIn("Requested 3 months", snapshot.warnings[0])
        self.assertNotIn("9999", snapshot.to_payload().values())

        six_months = ActualsSnapshotService(user=self.user, snapshot_date=date(2026, 9, 6)).create_snapshot(period="6m")
        twelve_months = ActualsSnapshotService(user=self.user, snapshot_date=date(2026, 9, 6)).create_snapshot(
            period="12m"
        )
        custom = ActualsSnapshotService(user=self.user, snapshot_date=date(2026, 9, 6)).create_snapshot(
            period="custom", custom_start_year=2026, custom_end_year=2026
        )
        self.assertEqual(six_months.month_count, 3)
        self.assertEqual(twelve_months.month_count, 3)
        self.assertEqual(custom.month_count, 3)
        self.assertNotIn("2026-09", custom.represented_months)

    def test_saved_tracked_snapshot_is_immutable_after_source_changes(self) -> None:
        source = DocumentSource.objects.create(
            user=self.user,
            source_type=DocumentSource.SourceType.DIRECT_UPLOAD,
            name="Tracked budget",
        )
        document = Document.objects.create(
            user=self.user,
            source=source,
            month=8,
            year=2026,
            total_income=Decimal("30000000"),
            total_expenses=Decimal("15000000"),
        )
        snapshot = ActualsSnapshotService(user=self.user, snapshot_date=date(2026, 9, 6)).create_snapshot(period="3m")
        saved = self.service.save_draft(
            user=self.user,
            draft=self.service.create_plan_with_draft(
                user=self.user,
                plan_data={"name": "Tracked"},
                scenario_data=_inputs(**snapshot.scenario_values()),
            ),
        )
        frozen_inputs = (saved.current_monthly_income, saved.current_monthly_expenses, saved.projection_data)
        document.total_income = Decimal("99999999")
        document.total_expenses = Decimal("1")
        document.save(update_fields=("total_income", "total_expenses"))
        saved.refresh_from_db()
        self.assertEqual(
            (saved.current_monthly_income, saved.current_monthly_expenses, saved.projection_data), frozen_inputs
        )
