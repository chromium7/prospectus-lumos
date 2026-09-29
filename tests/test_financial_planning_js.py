from __future__ import annotations

import re
from pathlib import Path

from django.contrib.staticfiles import finders
from django.test import SimpleTestCase

REPO_ROOT = Path(__file__).resolve().parent.parent
EVENTS_TEMPLATE = REPO_ROOT / "templates" / "financial_planning" / "wizard_events.html"

BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
LINE_COMMENT = re.compile(r"^[ \t]*//.*$", re.MULTILINE)


def _read_static(name: str) -> str:
    """Return a shipped script with its comments stripped.

    These tests assert what the code does and does not call, and the comments explain the very
    calls being ruled out — matching them would defeat the assertion.
    """

    path = finders.find(name)
    if path is None:
        raise AssertionError(f"{name} is not on the static files path")
    with open(path, encoding="utf-8") as handle:
        source = handle.read()
    return LINE_COMMENT.sub("", BLOCK_COMMENT.sub("", source))


class PlannerScriptContractTests(SimpleTestCase):
    """The shipped scripts must keep date and money derivation out of the browser."""

    def test_the_date_helpers_stay_on_the_local_calendar(self) -> None:
        script = _read_static("js/financial_planning_dates.js")
        # toISOString() serialises UTC, so reading a calendar day back out of it shifts the day for
        # anyone away from UTC; setMonth() overflows a short month instead of clamping to its end.
        self.assertNotIn("toISOString", script)
        self.assertNotIn("setMonth", script)
        self.assertIn("getFullYear()", script)
        self.assertIn("daysInMonth", script)
        self.assertIn("Math.min(day, daysInMonth", script)

    def test_the_events_script_uses_the_local_date_helpers(self) -> None:
        script = _read_static("js/financial_planning_events.js")
        self.assertIn("window.PlannerDates", script)
        self.assertIn("addMonths(today(), 12)", script)
        self.assertNotIn("toISOString", script)
        self.assertNotIn("setMonth", script)

    def test_the_events_page_loads_the_date_helpers_before_the_events_script(self) -> None:
        template = EVENTS_TEMPLATE.read_text(encoding="utf-8")
        self.assertLess(
            template.index("financial_planning_dates.js"),
            template.index("financial_planning_events.js"),
        )

    def test_the_review_script_never_formats_money_itself(self) -> None:
        script = _read_static("js/financial_planning_review.js")
        self.assertNotIn("toLocaleString", script)
        self.assertNotIn("Math.round", script)
        for field in (
            "required_monthly_investment_text",
            "total_target_text",
            "closing_balance_text",
            "target_text",
            "monthly_funding_need_text",
        ):
            self.assertIn(field, script)
