from __future__ import annotations

import json
import time
from datetime import date
from decimal import Decimal
from unittest import TestCase

from prospectus_lumos.apps.financial_planning.calculator import (
    CALCULATION_VERSION,
    MAX_PROJECTION_MONTHS,
    SCHEMA_VERSION,
    AchievementStatus,
    AmountBasis,
    CalculatorInputError,
    CalculatorInputs,
    DEFAULT_ANNUAL_CONTRIBUTION_GROWTH_RATE,
    DEFAULT_ANNUAL_INCOME_GROWTH_RATE,
    DEFAULT_ANNUAL_INFLATION_RATE,
    DEFAULT_ANNUAL_RETURN_RATE,
    DEFAULT_SAFETY_BUFFER_RATE,
    DEFAULT_WITHDRAWAL_RATE,
    EventInput,
    FinancialFreedomCalculator,
    FundingSource,
    InputErrorCode,
    ResultStatus,
    SolverStatus,
    WarningCode,
)

D = Decimal


def _inputs(**overrides: object) -> CalculatorInputs:
    values: dict[str, object] = {
        "calculation_date": date(2026, 9, 4),
        "target_date": date(2036, 9, 30),
        "desired_monthly_lifestyle": D("20000000"),
        "current_investable_assets": D("0"),
        "current_monthly_investment": D("10000000"),
        "current_monthly_income": D("30000000"),
        "current_monthly_expenses": D("20000000"),
        "withdrawal_rate": D("4"),
        "annual_return_rate": D("0"),
        "annual_inflation_rate": D("0"),
        "safety_buffer_rate": D("0"),
    }
    values.update(overrides)
    return CalculatorInputs(**values)  # type: ignore[arg-type]


class FinancialFreedomCalculatorTests(TestCase):
    def setUp(self) -> None:
        self.calculator = FinancialFreedomCalculator()

    def test_target_multiples_income_floor_and_inflation(self) -> None:
        simple = self.calculator.calculate(_inputs(), ())
        self.assertEqual(simple.target.base_freedom_number_today, D("6000000000"))

        rate_35 = self.calculator.calculate(_inputs(withdrawal_rate=D("3.5")), ())
        self.assertAlmostEqual(rate_35.target.base_freedom_number_today, D("6857142857.14"), delta=D("0.01"))

        funded = self.calculator.calculate(
            _inputs(desired_monthly_lifestyle=D("10000000"), post_freedom_monthly_income=D("12000000")), ()
        )
        self.assertEqual(funded.target.base_freedom_number_today, D("0"))
        self.assertIn(WarningCode.LIFESTYLE_FULLY_FUNDED, funded.warnings)

        inflated = self.calculator.calculate(_inputs(target_date=date(2027, 9, 1), annual_inflation_rate=D("12")), ())
        self.assertAlmostEqual(inflated.target.base_freedom_number_at_target, D("6720000000"), delta=D("0.01"))

    def test_defaults_and_stable_validation_error_states(self) -> None:
        defaults = CalculatorInputs(
            calculation_date=date(2026, 9, 1),
            target_date=date(2036, 9, 1),
            desired_monthly_lifestyle=D("20000000"),
        )
        self.assertEqual(defaults.withdrawal_rate, DEFAULT_WITHDRAWAL_RATE)
        self.assertEqual(defaults.annual_return_rate, DEFAULT_ANNUAL_RETURN_RATE)
        self.assertEqual(defaults.annual_inflation_rate, DEFAULT_ANNUAL_INFLATION_RATE)
        self.assertEqual(defaults.safety_buffer_rate, DEFAULT_SAFETY_BUFFER_RATE)
        self.assertEqual(defaults.annual_income_growth_rate, DEFAULT_ANNUAL_INCOME_GROWTH_RATE)
        self.assertEqual(defaults.annual_contribution_growth_rate, DEFAULT_ANNUAL_CONTRIBUTION_GROWTH_RATE)

        with self.assertRaises(CalculatorInputError) as captured:
            self.calculator.calculate(_inputs(withdrawal_rate=D("0")), ())
        self.assertEqual(captured.exception.code, InputErrorCode.INVALID_WITHDRAWAL_RATE)

    def test_zero_return_end_of_month_contributions_and_existing_assets_fixture(self) -> None:
        inputs = _inputs(
            target_date=date(2026, 11, 1),
            desired_monthly_lifestyle=D("1"),
            current_investable_assets=D("1000000000"),
            current_monthly_investment=D("10000000"),
        )
        projection = self.calculator.project(inputs.normalized(), (), D("10000000"), 2)
        self.assertEqual(projection.monthly[0].opening_balance, D("1000000000"))
        self.assertEqual(projection.monthly[0].growth, D("0"))
        self.assertEqual(projection.monthly[0].closing_balance, D("1010000000"))
        self.assertEqual(projection.ending_balance, D("1020000000"))

        ten_years = self.calculator.project(
            _inputs(current_investable_assets=D("1000000000")).normalized(), (), D("5000000"), 120
        )
        self.assertEqual(ten_years.ending_balance, D("1600000000"))

    def test_negative_return_and_contribution_growth_anniversary(self) -> None:
        inputs = _inputs(
            desired_monthly_lifestyle=D("1"),
            current_investable_assets=D("10000000"),
            annual_return_rate=D("-12"),
            annual_contribution_growth_rate=D("10"),
        ).normalized()
        projection = self.calculator.project(inputs, (), D("1000000"), 13)
        self.assertLess(projection.monthly[0].growth, D("0"))
        self.assertEqual(projection.monthly[11].contribution, D("1000000"))
        self.assertEqual(projection.monthly[12].contribution, D("1100000"))

    def test_car_event_fixture_inclusive_range_and_shortfall(self) -> None:
        car = EventInput(
            name="Car",
            event_date=date(2029, 9, 17),
            one_time_amount=D("300000000"),
            recurring_monthly_amount=D("5000000"),
            recurring_end_date=date(2032, 8, 2),
            amount_basis=AmountBasis.EVENT_DATE,
        )
        inputs = _inputs(desired_monthly_lifestyle=D("1")).normalized()
        projection = self.calculator.project(inputs, (car,), D("10000000"), 72)
        outflow_rows = [row for row in projection.monthly if row.event_outflow]
        self.assertEqual(len(outflow_rows), 36)
        self.assertEqual(outflow_rows[0].event_outflow, D("305000000"))
        self.assertEqual(outflow_rows[-1].event_outflow, D("5000000"))
        self.assertEqual(sum((row.event_outflow for row in outflow_rows), D("0")), D("480000000"))
        self.assertTrue(all(row.closing_balance >= D("0") for row in projection.monthly))
        without_car = self.calculator.project(inputs, (), D("10000000"), 36)
        self.assertEqual(without_car.ending_balance - projection.monthly[35].closing_balance, D("305000000"))

        shortfall = self.calculator.project(inputs, (car,), D("0"), 36)
        self.assertGreater(shortfall.total_event_shortfall, D("0"))

    def test_separate_house_fixture_is_disclosed_without_portfolio_effect(self) -> None:
        house = EventInput(
            name="House",
            event_date=date(2027, 9, 1),
            one_time_amount=D("1200000000"),
            amount_basis=AmountBasis.EVENT_DATE,
            funding_source=FundingSource.SEPARATE_SAVINGS,
        )
        inputs = _inputs(desired_monthly_lifestyle=D("1"), target_date=date(2028, 9, 1))
        with_event = self.calculator.calculate(inputs, (house,))
        without_event = self.calculator.calculate(inputs, ())
        self.assertEqual(with_event.projection.ending_balance, without_event.projection.ending_balance)
        self.assertEqual(with_event.target.total_target, without_event.target.total_target)
        self.assertEqual(with_event.separate_savings[0].nominal_total, D("1200000000"))
        self.assertEqual(with_event.separate_savings[0].monthly_funding_need, D("100000000"))
        self.assertIn(WarningCode.SEPARATE_SAVINGS, with_event.warnings)

    def test_post_target_reserve_is_included_exactly_once(self) -> None:
        event = EventInput(
            name="Later cost",
            event_date=date(2028, 9, 1),
            one_time_amount=D("120000000"),
            amount_basis=AmountBasis.EVENT_DATE,
        )
        inputs = _inputs(
            target_date=date(2027, 9, 1),
            desired_monthly_lifestyle=D("0"),
            annual_return_rate=D("0"),
        )
        result = self.calculator.calculate(inputs, (event,))
        self.assertEqual(result.target.post_target_event_reserve, D("120000000"))
        self.assertEqual(result.target.total_target, D("120000000"))
        self.assertEqual(result.cases["base"].solver.monthly_contribution, D("10000000"))

    def test_solver_known_value_complete_and_unreachable_fixtures(self) -> None:
        known = self.calculator.calculate(
            _inputs(
                target_date=date(2027, 9, 1),
                desired_monthly_lifestyle=D("40000"),
                current_monthly_investment=D("1000000"),
            ),
            (),
        )
        self.assertEqual(known.target.total_target, D("12000000"))
        self.assertLessEqual(abs((known.cases["base"].solver.monthly_contribution or D("0")) - D("1000000")), D("1000"))

        complete = self.calculator.calculate(
            _inputs(desired_monthly_lifestyle=D("1000000"), current_investable_assets=D("400000000")), ()
        )
        self.assertEqual(complete.cases["base"].solver.monthly_contribution, D("0"))
        self.assertEqual(complete.status, ResultStatus.COMPLETE)

        unreachable = self.calculator.calculate(
            _inputs(
                target_date=date(2026, 10, 1),
                desired_monthly_lifestyle=D("100000000000"),
                annual_return_rate=D("-99"),
            ),
            (),
        )
        self.assertEqual(unreachable.cases["base"].solver.status, SolverStatus.UNREACHABLE)
        self.assertIsNone(unreachable.cases["base"].solver.monthly_contribution)

    def test_achievement_is_first_sustainable_crossing_and_horizon_terminates(self) -> None:
        later_event = EventInput(
            name="Future",
            event_date=date(2027, 11, 1),
            one_time_amount=D("10000000"),
            amount_basis=AmountBasis.EVENT_DATE,
        )
        inputs = _inputs(
            target_date=date(2027, 9, 1),
            desired_monthly_lifestyle=D("0"),
            current_monthly_investment=D("1000000"),
        ).normalized()
        achievement = self.calculator.find_achievement_date(inputs, (later_event,))
        self.assertEqual(achievement.status, AchievementStatus.REACHED)
        self.assertEqual(achievement.achievement_date, date(2027, 7, 1))

        missed_event = EventInput(
            name="Unfunded event",
            event_date=date(2026, 10, 1),
            one_time_amount=D("2000000"),
            amount_basis=AmountBasis.EVENT_DATE,
        )
        missed = self.calculator.find_achievement_date(inputs, (missed_event,))
        self.assertEqual(missed.status, AchievementStatus.NOT_REACHED)

        never = self.calculator.find_achievement_date(
            _inputs(
                desired_monthly_lifestyle=D("1000000000"),
                current_monthly_investment=D("0"),
                annual_return_rate=D("0"),
                annual_inflation_rate=D("0"),
            ).normalized(),
            (),
        )
        self.assertEqual(never.status, AchievementStatus.NOT_REACHED)
        self.assertIsNone(never.achievement_date)
        projection = self.calculator.project(_inputs().normalized(), (), D("0"), MAX_PROJECTION_MONTHS)
        self.assertEqual(len(projection.monthly), 1200)

    def test_scenario_ranges_use_documented_adjustments_and_lower_bounds(self) -> None:
        result = self.calculator.calculate(_inputs(annual_return_rate=D("-98"), withdrawal_rate=D("0.25")), ())
        self.assertEqual(result.cases["conservative"].annual_return_rate, D("-99"))
        self.assertEqual(result.cases["conservative"].withdrawal_rate, D("0.25"))
        self.assertEqual(result.cases["base"].annual_return_rate, D("-98"))
        self.assertEqual(result.cases["optimistic"].annual_return_rate, D("-96"))

    def test_income_growth_is_used_only_for_affordability_comparison(self) -> None:
        result = self.calculator.calculate(
            _inputs(
                target_date=date(2028, 9, 1),
                current_monthly_income=D("20000000"),
                current_monthly_expenses=D("10000000"),
                annual_income_growth_rate=D("10"),
                annual_inflation_rate=D("0"),
            ),
            (),
        )
        self.assertEqual(result.current_monthly_surplus, D("10000000"))
        self.assertEqual(result.target_monthly_income, D("24200000"))
        self.assertEqual(result.target_monthly_expenses, D("10000000"))
        self.assertEqual(result.target_monthly_surplus, D("14200000"))

    def test_event_today_money_inflates_to_each_payment_month(self) -> None:
        event = EventInput(
            name="Education",
            event_date=date(2027, 9, 1),
            recurring_monthly_amount=D("1000000"),
            recurring_end_date=date(2027, 10, 1),
        )
        inputs = _inputs(annual_inflation_rate=D("12"), desired_monthly_lifestyle=D("1")).normalized()
        projection = self.calculator.project(inputs, (event,), D("0"), 13)
        self.assertAlmostEqual(projection.monthly[11].event_outflow, D("1120000"), delta=D("0.01"))
        self.assertGreater(projection.monthly[12].event_outflow, projection.monthly[11].event_outflow)

    def test_serialization_is_stable_versioned_and_contains_no_floats(self) -> None:
        result_a = self.calculator.calculate(_inputs(), ())
        result_b = self.calculator.calculate(
            _inputs(calculation_date=date(2026, 9, 30), target_date=date(2036, 9, 2)), ()
        )
        serialized_a = result_a.to_json()
        serialized_b = result_b.to_json()
        self.assertEqual(serialized_a.encode(), serialized_b.encode())
        payload = json.loads(serialized_a)
        self.assertEqual(payload["schema_version"], SCHEMA_VERSION)
        self.assertEqual(payload["calculation_version"], CALCULATION_VERSION)
        self.assertEqual(payload["target_breakdown"]["total_target"], "6000000000.00")
        self.assertNotIn(".0,", serialized_a)

    def test_representative_benchmark_under_500_ms(self) -> None:
        events = tuple(
            EventInput(
                name=f"Event {index}",
                event_date=date(2030 + index, 9, 1),
                one_time_amount=D("100000000"),
            )
            for index in range(10)
        )
        inputs = _inputs(
            target_date=date(2086, 9, 1),
            annual_return_rate=D("7"),
            annual_inflation_rate=D("3"),
        )
        started = time.perf_counter()
        self.calculator.calculate(inputs, events)
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 0.5, f"Representative calculation took {elapsed:.3f}s")
