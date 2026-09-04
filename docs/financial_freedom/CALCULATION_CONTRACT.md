# Financial Freedom Calculation Contract (version 1)

The public interface is `FinancialFreedomCalculator.calculate(inputs, events)`. Inputs are immutable plain dataclasses;
the calculator has no Django model, request, view, or database dependency. Percentage values use percentage points, so
`Decimal("7")` means 7%. Authoritative calculations use `Decimal` without monthly rounding.

Dates normalize to the first day of their calendar month. The calculation month is month zero (the opening snapshot),
and the first ledger row is the following month. Growth occurs first, the regular contribution arrives at month end,
then life-event outflows are deducted. A recurring event includes both its start and end month. A target-month event is
deducted during accumulation; only later portfolio-funded event payments become target reserves. Today's-money recurring
payments inflate separately for every payment month. Event-date-basis recurring payments remain nominally constant.

Money serializes as a two-decimal string using `ROUND_HALF_UP`; rates serialize as strings with three decimals. Stable
JSON uses sorted keys and compact separators. Payloads declare `schema_version=1`, `calculation_version=1`, and `IDR`.

The required-contribution solver starts at Rp0/Rp10,000,000, doubles its upper bound through Rp1,000,000,000,000, stops
within Rp1,000/month or 64 bisections, and reports `unreachable_within_solver_bounds` rather than inventing a value. The
projection horizon is 1,200 months. Accepted annual return, inflation, income-growth, and contribution-growth inputs are
-99% through 100%; this keeps every compound factor positive. Withdrawal accepts (0%, 10%], safety buffer 0% through
100%, and at most 50 events. Conservative return has a -99% floor. Conservative withdrawal has a 0.5% normal floor,
but never rises above an unusually low base withdrawal rate.

Portfolio-funded payments before or in the target month reduce accumulation. Later payments are discounted to a single
target-date reserve and are not otherwise added to the target. Outflows above available assets record a shortfall and
leave a zero balance. Separately funded events neither reduce the portfolio nor increase its target; each is disclosed
with its nominal total and straight-line monthly funding need through its start month.

Affordability is informational and does not alter contributions: current surplus is income less expenses (floored at
zero), while target-month surplus grows income on whole scenario anniversaries and inflates expenses monthly.

Deterministic fixture expectations are executable in `tests/test_financial_planning_calculator.py`: the 25x fixture is
Rp6,000,000,000; the existing-assets fixture ends at Rp1,600,000,000; the car fixture deducts Rp300,000,000 followed by
36 inclusive Rp5,000,000 payments; the separate house fixture leaves the portfolio unchanged and discloses
Rp1,200,000,000 / Rp100,000,000 monthly; the complete fixture requires Rp0; and the unreachable fixture returns the
explicit solver failure state.
