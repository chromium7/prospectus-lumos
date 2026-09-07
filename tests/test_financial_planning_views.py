from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.contrib.staticfiles import finders
from django.test import Client, TestCase
from django.urls import reverse

from prospectus_lumos.apps.accounts.models import DocumentSource
from prospectus_lumos.apps.documents.models import Document
from prospectus_lumos.apps.financial_planning.models import FreedomPlan, FreedomScenario
from prospectus_lumos.website.financial_planning.forms import EVENT_PRESETS


def _payload(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "name": "Freedom at 45",
        "description": "Manual plan",
        "calculation_date": "2026-09-04",
        "birth_date": "",
        "target_date": "2046-09-01",
        "current_monthly_income": "30000000",
        "current_monthly_expenses": "15000000",
        "current_monthly_investment": "10000000",
        "current_investable_assets": "100000000",
        "emergency_savings": "50000000",
        "desired_monthly_lifestyle": "20000000",
        "post_freedom_monthly_income": "0",
        "withdrawal_rate": "4",
        "annual_return_rate": "7",
        "annual_inflation_rate": "3",
        "annual_income_growth_rate": "0",
        "annual_contribution_growth_rate": "0",
        "safety_buffer_rate": "10",
    }
    values.update(overrides)
    return values


def _event_payload(events: list[dict[str, object]], *, initial_forms: int = 0) -> dict[str, object]:
    values: dict[str, object] = {
        "events-TOTAL_FORMS": str(len(events)),
        "events-INITIAL_FORMS": str(initial_forms),
        "events-MIN_NUM_FORMS": "0",
        "events-MAX_NUM_FORMS": "50",
    }
    for index, event in enumerate(events):
        for field, value in event.items():
            values[f"events-{index}-{field}"] = value
    return values


class FinancialPlanningViewTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(username="planner", password="pass")
        self.other = User.objects.create_user(username="other", password="pass")

    def _create_draft(self) -> FreedomScenario:
        self.client.login(username="planner", password="pass")
        response = self.client.post(reverse("freedom_plan_create"), _payload())
        self.assertEqual(response.status_code, 302)
        return FreedomScenario.objects.get(plan__user=self.user)

    def test_every_route_requires_authentication(self) -> None:
        plan = FreedomPlan.objects.create(user=self.user, name="Private")
        scenario = FreedomScenario.objects.create(
            plan=plan,
            calculation_date=date(2026, 9, 4),
            target_date=date(2046, 9, 1),
            desired_monthly_lifestyle=20000000,
        )
        urls = (
            reverse("freedom_plan_list"),
            reverse("freedom_plan_create"),
            reverse("freedom_actuals_preview"),
            reverse("freedom_calculate_preview"),
            reverse("freedom_plan_draft", args=(plan.pk,)),
            reverse("freedom_plan_save", args=(plan.pk,)),
            reverse("freedom_scenario_detail", args=(plan.pk, scenario.pk)),
            reverse("freedom_scenario_update", args=(plan.pk, scenario.pk)),
            reverse("freedom_scenario_duplicate", args=(plan.pk, scenario.pk)),
            reverse("freedom_plan_rename", args=(plan.pk,)),
            reverse("freedom_plan_archive", args=(plan.pk,)),
            reverse("freedom_plan_restore", args=(plan.pk,)),
        )
        for url in urls:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302)
            self.assertIn(reverse("login"), response.headers["Location"])

    def test_manual_create_recalculate_and_save_ignores_posted_outputs(self) -> None:
        draft = self._create_draft()
        response = self.client.get(reverse("freedom_plan_draft", args=(draft.plan_id,)))
        self.assertTemplateUsed(response, "financial_planning/plan_builder.html")

        response = self.client.post(
            reverse("freedom_plan_draft", args=(draft.plan_id,)),
            _payload(desired_monthly_lifestyle="25000000", total_target="1"),
        )
        self.assertEqual(response.status_code, 302)
        draft.refresh_from_db()
        self.assertEqual(draft.desired_monthly_lifestyle, 25000000)
        self.assertGreater(draft.total_target, 1)

        response = self.client.post(
            reverse("freedom_plan_save", args=(draft.plan_id,)),
            _payload(desired_monthly_lifestyle="30000000", total_target="1"),
        )
        self.assertEqual(response.status_code, 302)
        draft.refresh_from_db()
        self.assertEqual(draft.status, FreedomScenario.Status.SAVED)
        self.assertEqual(draft.version, 1)
        self.assertGreater(draft.total_target, 1)
        self.assertRedirects(response, reverse("freedom_scenario_detail", args=(draft.plan_id, draft.pk)))

    def test_invalid_form_preserves_input(self) -> None:
        draft = self._create_draft()
        response = self.client.post(
            reverse("freedom_plan_draft", args=(draft.plan_id,)),
            _payload(target_date="2026-09-01", desired_monthly_lifestyle="12345678"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Target date must be after")
        self.assertContains(response, "12345678")

    def test_foreign_objects_are_404_and_mutations_are_post_only(self) -> None:
        draft = self._create_draft()
        plan_id = draft.plan_id
        self.client.login(username="other", password="pass")
        for url in (
            reverse("freedom_plan_draft", args=(plan_id,)),
            reverse("freedom_plan_save", args=(plan_id,)),
            reverse("freedom_plan_archive", args=(plan_id,)),
        ):
            self.assertEqual(self.client.post(url).status_code, 404)
        detail_url = reverse("freedom_scenario_detail", args=(plan_id, draft.pk))
        self.assertEqual(self.client.get(detail_url).status_code, 404)

        self.client.login(username="planner", password="pass")
        for url in (
            reverse("freedom_plan_save", args=(plan_id,)),
            reverse("freedom_plan_archive", args=(plan_id,)),
            reverse("freedom_plan_restore", args=(plan_id,)),
            reverse("freedom_plan_rename", args=(plan_id,)),
        ):
            self.assertEqual(self.client.get(url).status_code, 405)

    def test_update_duplicate_rename_archive_and_restore(self) -> None:
        saved = self._create_draft()
        self.client.post(reverse("freedom_plan_save", args=(saved.plan_id,)))
        saved.refresh_from_db()
        original_target = saved.total_target

        response = self.client.post(reverse("freedom_scenario_update", args=(saved.plan_id, saved.pk)))
        self.assertEqual(response.status_code, 302)
        clone = FreedomScenario.objects.get(plan_id=saved.plan_id, status=FreedomScenario.Status.DRAFT)
        self.assertEqual(clone.based_on, saved)
        saved.refresh_from_db()
        self.assertEqual(saved.total_target, original_target)

        response = self.client.post(reverse("freedom_scenario_duplicate", args=(saved.plan_id, saved.pk)))
        duplicate = FreedomScenario.objects.exclude(plan_id=saved.plan_id).get()
        self.assertRedirects(response, reverse("freedom_plan_draft", args=(duplicate.plan_id,)))

        self.client.post(reverse("freedom_plan_rename", args=(saved.plan_id,)), {"name": "Renamed"})
        saved.plan.refresh_from_db()
        self.assertEqual(saved.plan.name, "Renamed")
        self.client.post(reverse("freedom_plan_archive", args=(saved.plan_id,)))
        saved.plan.refresh_from_db()
        self.assertTrue(saved.plan.is_archived)
        self.client.post(reverse("freedom_plan_restore", args=(saved.plan_id,)))
        saved.plan.refresh_from_db()
        self.assertFalse(saved.plan.is_archived)

    def test_csrf_is_enforced_for_save(self) -> None:
        draft = self._create_draft()
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.login(username="planner", password="pass")
        response = csrf_client.post(reverse("freedom_plan_save", args=(draft.plan_id,)))
        self.assertEqual(response.status_code, 403)

    def test_non_javascript_event_create_reorder_delete_and_invalid_preservation(self) -> None:
        self.client.login(username="planner", password="pass")
        car: dict[str, object] = {
            "name": "Car",
            "category": "car",
            "event_date": "2030-01-01",
            "one_time_amount": "300000000",
            "amount_basis": "today",
            "recurring_monthly_amount": "5000000",
            "recurring_end_date": "2032-12-01",
            "funding_source": "investment_portfolio",
            "sort_order": "0",
            "notes": "Editable preset",
        }
        home: dict[str, object] = {
            "name": "Home deposit",
            "category": "home",
            "event_date": "2031-01-01",
            "one_time_amount": "500000000",
            "amount_basis": "today",
            "recurring_monthly_amount": "0",
            "recurring_end_date": "",
            "funding_source": "separate_savings",
            "sort_order": "1",
            "notes": "",
        }
        response = self.client.post(reverse("freedom_plan_create"), {**_payload(), **_event_payload([car, home])})
        self.assertEqual(response.status_code, 302)
        draft = FreedomScenario.objects.get(plan__user=self.user)
        self.assertEqual(list(draft.events.values_list("name", flat=True)), ["Car", "Home deposit"])
        self.assertGreater(draft.required_monthly_investment, 0)
        self.assertEqual(len(draft.projection_data["separate_savings"]), 1)

        current = list(draft.events.order_by("sort_order"))
        car.update({"id": current[0].pk, "sort_order": "1"})
        home.update({"id": current[1].pk, "sort_order": "0"})
        response = self.client.post(
            reverse("freedom_plan_draft", args=(draft.plan_id,)),
            {**_payload(), **_event_payload([car, home], initial_forms=2)},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(list(draft.events.values_list("name", flat=True)), ["Home deposit", "Car"])

        current = list(draft.events.order_by("sort_order"))
        delete_home = {**home, "id": current[0].pk, "sort_order": "0", "DELETE": "on"}
        keep_car = {**car, "id": current[1].pk, "sort_order": "1"}
        response = self.client.post(
            reverse("freedom_plan_draft", args=(draft.plan_id,)),
            {**_payload(), **_event_payload([delete_home, keep_car], initial_forms=2)},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(list(draft.events.values_list("name", flat=True)), ["Car"])

        invalid = {**home, "name": "Keep this input", "one_time_amount": "0", "event_date": "2026-01-01"}
        response = self.client.post(
            reverse("freedom_plan_create"),
            {**_payload(name="Invalid events"), **_event_payload([car, invalid])},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Keep this input")
        self.assertContains(response, "positive cost")
        self.assertFalse(FreedomPlan.objects.filter(name="Invalid events").exists())

    def test_preview_schema_stays_server_authoritative_and_owned(self) -> None:
        draft = self._create_draft()
        event: dict[str, object] = {
            "name": "Separate house fund",
            "category": "home",
            "event_date": "2032-01-01",
            "one_time_amount": "500000000",
            "amount_basis": "today",
            "recurring_monthly_amount": "0",
            "recurring_end_date": "",
            "funding_source": "separate_savings",
            "sort_order": "0",
            "notes": "",
        }
        response = self.client.post(
            reverse("freedom_calculate_preview"),
            {
                **_payload(total_target="1"),
                **_event_payload([event]),
                "plan_id": draft.plan_id,
                "preview_request_id": "newest-7",
            },
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["request_id"], "newest-7")
        self.assertEqual(data["schema_version"], 1)
        self.assertIsInstance(data["summary"]["total_target"], str)
        self.assertGreater(Decimal(data["summary"]["total_target"]), Decimal("1"))
        self.assertTrue(all(Decimal(row["event_outflow"]) == 0 for row in data["timeline"]))
        self.assertEqual(data["separate_savings"][0]["name"], "Separate house fund")

        self.client.login(username="other", password="pass")
        forbidden = self.client.post(
            reverse("freedom_calculate_preview"),
            {**_payload(), **_event_payload([]), "plan_id": draft.plan_id},
        )
        self.assertEqual(forbidden.status_code, 404)

    def test_tracked_actuals_are_editable_and_source_metadata_is_saved(self) -> None:
        source = DocumentSource.objects.create(
            user=self.user,
            source_type=DocumentSource.SourceType.DIRECT_UPLOAD,
            name="Budget",
        )
        Document.objects.create(
            user=self.user,
            source=source,
            month=8,
            year=2026,
            total_income=30000000,
            total_expenses=15000000,
        )
        self.client.login(username="planner", password="pass")
        response = self.client.get(reverse("freedom_plan_create") + "?source=tracked_actuals&period=3m")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="30000000.00"')
        self.assertContains(response, "1 represented complete month")

        response = self.client.post(
            reverse("freedom_plan_create"),
            _payload(
                name="Tracked override",
                source_mode="tracked_actuals",
                source_period_start="2026-06-01",
                source_period_end="2026-08-01",
                source_month_count="1",
                source_excluded_income_categories="[]",
                source_excluded_expense_categories="[]",
                current_monthly_income="31000000",
                current_monthly_expenses="14000000",
                current_monthly_investment="17000000",
            ),
        )
        self.assertEqual(response.status_code, 302)
        draft = FreedomScenario.objects.get(plan__name="Tracked override")
        self.assertEqual(draft.source_mode, FreedomScenario.SourceMode.TRACKED_ACTUALS)
        self.assertEqual(draft.source_month_count, 1)
        self.assertEqual(draft.source_period_start, date(2026, 6, 1))
        self.assertEqual(draft.current_monthly_income, 31000000)
        self.assertEqual(draft.current_monthly_expenses, 14000000)

        actuals_response = self.client.post(reverse("freedom_actuals_preview"), {"period": "3m"})
        self.assertEqual(actuals_response.status_code, 200)
        actuals_data = actuals_response.json()["snapshot"]
        self.assertEqual(actuals_data["month_count"], 1)
        self.assertIsInstance(actuals_data["average_income"], str)

    def test_portfolio_analyzer_links_owned_filter_range_and_presets_are_complete(self) -> None:
        self.client.login(username="planner", password="pass")
        response = self.client.get(reverse("portfolio_analyzer") + "?year_from=2025&year_to=2026")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Create a Financial Freedom Plan")
        self.assertContains(
            response,
            "source=tracked_actuals&amp;period=custom&amp;start_year=2025&amp;end_year=2026",
        )
        self.assertEqual(
            set(EVENT_PRESETS),
            {"car", "home", "wedding", "education", "business", "medical", "family", "custom"},
        )

    def test_event_formset_caps_rows_and_preview_script_cancels_stale_requests(self) -> None:
        self.client.login(username="planner", password="pass")
        events: list[dict[str, object]] = [
            {
                "name": f"Event {index}",
                "category": "custom",
                "event_date": "2030-01-01",
                "one_time_amount": "1",
                "amount_basis": "event_date",
                "recurring_monthly_amount": "0",
                "recurring_end_date": "",
                "funding_source": "investment_portfolio",
                "sort_order": str(index),
                "notes": "",
            }
            for index in range(51)
        ]
        response = self.client.post(
            reverse("freedom_plan_create"),
            {**_payload(name="Too many events"), **_event_payload(events)},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "at most 50", html=False)
        self.assertFalse(FreedomPlan.objects.filter(name="Too many events").exists())

        script_path = finders.find("js/financial_planning.js")
        self.assertIsNotNone(script_path)
        with open(script_path, encoding="utf-8") as script_file:
            script = script_file.read()
        self.assertIn("new AbortController()", script)
        self.assertIn("sequence !== previewSequence", script)
        self.assertIn("data.request_id !== String(sequence)", script)
