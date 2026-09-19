from __future__ import annotations

import os
import shutil
import subprocess
import unittest
from pathlib import Path

from django.contrib.staticfiles import finders
from django.test import SimpleTestCase

NODE = shutil.which("node")

# Asia/Jakarta is the product's default timezone and sits east of UTC; Pacific/Honolulu sits west.
# A date helper that leaks UTC drifts a day in one direction or the other, so both are exercised.
TIMEZONES = ("Asia/Jakarta", "UTC", "Pacific/Honolulu")

REPO_ROOT = Path(__file__).resolve().parent.parent
DATE_TEST_FILE = REPO_ROOT / "tests" / "js" / "financial_planning_dates.test.js"
EVENTS_TEMPLATE = REPO_ROOT / "templates" / "financial_planning" / "wizard_events.html"


def _read_static(name: str) -> str:
    path = finders.find(name)
    if path is None:
        raise AssertionError(f"{name} is not on the static files path")
    with open(path, encoding="utf-8") as handle:
        return handle.read()


@unittest.skipIf(NODE is None, "node is required to run the planner's JavaScript tests")
class PlannerDateHelperTests(SimpleTestCase):
    """The preset date helpers are run under node, once per timezone."""

    def test_date_helpers_hold_in_every_timezone(self) -> None:
        for timezone_name in TIMEZONES:
            with self.subTest(timezone=timezone_name):
                result = subprocess.run(
                    [str(NODE), "--test", str(DATE_TEST_FILE)],
                    env={**os.environ, "TZ": timezone_name},
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class PlannerScriptContractTests(SimpleTestCase):
    """The shipped scripts must keep date and money derivation out of the browser."""

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
