from __future__ import annotations

from typing import Any, cast
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from prospectus_lumos.apps.financial_planning.models import FreedomPlan, FreedomScenario
from prospectus_lumos.apps.financial_planning.services import (
    ActualsSnapshot,
    ActualsSnapshotService,
    DraftAlreadyExistsError,
    FreedomScenarioService,
)
from prospectus_lumos.core.utils import TypedHttpRequest

from .forms import (
    EVENT_PRESETS,
    ActualsSnapshotForm,
    BaseFinancialEventFormSet,
    FinancialEventFormSet,
    FreedomPlanForm,
    FreedomScenarioForm,
    GoalStepForm,
    MoneyStepForm,
    ReviewStepForm,
    ScenarioCompareForm,
    event_formset_values,
)
from .comparison import compare_scenarios
from .results import STATUS_COPY, scenario_result_context, timeline_chart_payload, timeline_summary

WIZARD_STEPS = ((1, "Your goal"), (2, "Your money"), (3, "Life events"), (4, "Review"))


def _tracked_source_query(request: TypedHttpRequest) -> str:
    if request.GET.get("source") != FreedomScenario.SourceMode.TRACKED_ACTUALS:
        return ""
    values = {"source": FreedomScenario.SourceMode.TRACKED_ACTUALS, "period": request.GET.get("period", "12m")}
    if values["period"] == "custom":
        values.update({"start_year": request.GET.get("start_year", ""), "end_year": request.GET.get("end_year", "")})
    return urlencode(values)


def _snapshot_from_form(request: TypedHttpRequest, form: ActualsSnapshotForm) -> ActualsSnapshot | None:
    if not form.is_valid():
        return None
    return ActualsSnapshotService(user=request.user).create_snapshot(**form.snapshot_options())


def _actuals_form_from_query(request: TypedHttpRequest) -> tuple[ActualsSnapshotForm, ActualsSnapshot | None]:
    if request.GET.get("source") != FreedomScenario.SourceMode.TRACKED_ACTUALS:
        return ActualsSnapshotForm(user=request.user), None
    query_data = {
        "period": request.GET.get("period", "12m"),
        "start_year": request.GET.get("start_year", ""),
        "end_year": request.GET.get("end_year", ""),
    }
    form = ActualsSnapshotForm(query_data, user=request.user)
    return form, _snapshot_from_form(request, form)


def _event_formset(
    request: TypedHttpRequest,
    *,
    draft: FreedomScenario,
) -> BaseFinancialEventFormSet:
    formset = cast(
        BaseFinancialEventFormSet,
        FinancialEventFormSet(
            request.POST or None,
            instance=draft,
            prefix="events",
            calculation_date=draft.calculation_date,
        ),
    )
    if request.method == "GET" and draft.events.exists():
        formset.extra = 0
    return formset


def _preview_scenario_values(draft: FreedomScenario, overrides: dict[str, Any]) -> dict[str, Any]:
    values = {field_name: getattr(draft, field_name) for field_name in FreedomScenarioForm.Meta.fields}
    values.update(overrides)
    return values


def _preview_event_values(draft: FreedomScenario) -> list[dict[str, Any]]:
    return [
        {
            "name": event.name,
            "event_date": event.event_date,
            "one_time_amount": event.one_time_amount,
            "recurring_monthly_amount": event.recurring_monthly_amount,
            "recurring_end_date": event.recurring_end_date,
            "amount_basis": event.amount_basis,
            "funding_source": event.funding_source,
        }
        for event in draft.events.all()
    ]


def _review_context(
    *,
    plan: FreedomPlan,
    draft: FreedomScenario,
    review_form: ReviewStepForm,
) -> dict[str, object]:
    monthly = draft.projection_data.get("monthly", [])
    timeline = [row for index, row in enumerate(monthly) if index % 60 == 0 or index == len(monthly) - 1]
    required = draft.required_monthly_investment or 0
    return {
        "plan": plan,
        "draft": draft,
        "review_form": review_form,
        "monthly_increase": max(0, required - draft.current_monthly_investment),
        "timeline": timeline,
        "separate_savings": draft.projection_data.get("separate_savings", []),
        "current_step": 4,
        "wizard_steps": WIZARD_STEPS,
        "selected_tab": "financial_freedom",
    }


def _progress_width(scenario: FreedomScenario | None) -> int:
    """Clamp stored progress to a 0-100 bar width without changing the reported percentage."""

    if scenario is None:
        return 0
    return max(0, min(100, int(scenario.progress_percent)))


def _owned_plan(request: TypedHttpRequest, plan_id: int) -> FreedomPlan:
    return get_object_or_404(FreedomPlan, pk=plan_id, user=request.user)


def _owned_scenario(request: TypedHttpRequest, plan_id: int, scenario_id: int) -> FreedomScenario:
    return get_object_or_404(
        FreedomScenario.objects.select_related("plan").prefetch_related("events"),
        pk=scenario_id,
        plan_id=plan_id,
        plan__user=request.user,
    )


def _builder_context(
    *,
    plan: FreedomPlan | None,
    draft: FreedomScenario | None,
    form: FreedomScenarioForm,
    plan_form: FreedomPlanForm | None,
) -> dict[str, object]:
    return {
        "plan": plan,
        "draft": draft,
        "form": form,
        "plan_form": plan_form,
        "selected_tab": "financial_freedom",
    }


@login_required
@require_GET
def plan_list_view(request: TypedHttpRequest) -> HttpResponse:
    """List the current user's active or archived plans with prefetched scenarios."""

    show_archived = request.GET.get("archived") == "1"
    scenarios = FreedomScenario.objects.order_by("-version", "-created_at").prefetch_related("events")
    plans = list(
        FreedomPlan.objects.filter(user=request.user, is_archived=show_archived).prefetch_related(
            Prefetch("scenarios", queryset=scenarios, to_attr="library_scenarios")
        )
    )
    for plan in plans:
        setattr(
            plan,
            "library_draft",
            next(
                (scenario for scenario in plan.library_scenarios if scenario.status == FreedomScenario.Status.DRAFT),
                None,
            ),
        )
        saved = [scenario for scenario in plan.library_scenarios if scenario.status == FreedomScenario.Status.SAVED]
        latest = saved[0] if saved else None
        setattr(plan, "library_latest", latest)
        setattr(plan, "library_saved_count", len(saved))
        setattr(plan, "library_status", STATUS_COPY.get(latest.result_status) if latest else None)
        setattr(plan, "library_event_count", latest.events.count() if latest else 0)
        setattr(plan, "library_progress", _progress_width(latest))
    return render(
        request,
        "financial_planning/plan_list.html",
        {"plans": plans, "show_archived": show_archived, "selected_tab": "financial_freedom"},
    )


@login_required
@require_http_methods(["GET", "POST"])
def plan_create_view(request: TypedHttpRequest) -> HttpResponse:
    """Start a plan with one approachable page about the user's goal."""

    plan_form = FreedomPlanForm(request.POST or None)
    initial = FreedomScenarioForm.initial_values()
    goal_form = GoalStepForm(request.POST or None, initial=initial)
    if request.method == "POST" and plan_form.is_valid() and goal_form.is_valid():
        draft = FreedomScenarioService().create_plan_with_draft(
            user=request.user,
            plan_data=plan_form.cleaned_data,
            scenario_data={**initial, **goal_form.scenario_values()},
        )
        messages.success(request, "Great start. Next, tell us what your money looks like today.")
        money_url = reverse("freedom_plan_money", args=(draft.plan_id,))
        source_query = _tracked_source_query(request)
        return redirect(f"{money_url}?{source_query}" if source_query else money_url)
    return render(
        request,
        "financial_planning/wizard_goal.html",
        {
            "plan": None,
            "plan_form": plan_form,
            "goal_form": goal_form,
            "current_step": 1,
            "wizard_steps": WIZARD_STEPS,
            "selected_tab": "financial_freedom",
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def plan_goal_view(request: TypedHttpRequest, plan_id: int) -> HttpResponse:
    """Edit the goal page for an owned draft without exposing other inputs."""

    plan = _owned_plan(request, plan_id)
    draft = get_object_or_404(FreedomScenario, plan=plan, status=FreedomScenario.Status.DRAFT)
    plan_form = FreedomPlanForm(request.POST or None, instance=plan)
    goal_form = GoalStepForm(request.POST or None, instance=draft)
    if request.method == "POST" and plan_form.is_valid() and goal_form.is_valid():
        updated_plan = plan_form.save(commit=False)
        updated_plan.user = request.user
        updated_plan.save(update_fields=("name", "description", "updated_at"))
        FreedomScenarioService().update_draft(
            user=request.user,
            draft=draft,
            scenario_data=goal_form.scenario_values(),
        )
        messages.success(request, "Your goal is updated.")
        return redirect("freedom_plan_money", plan_id=plan.pk)
    return render(
        request,
        "financial_planning/wizard_goal.html",
        {
            "plan": plan,
            "plan_form": plan_form,
            "goal_form": goal_form,
            "current_step": 1,
            "wizard_steps": WIZARD_STEPS,
            "selected_tab": "financial_freedom",
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def plan_money_view(request: TypedHttpRequest, plan_id: int) -> HttpResponse:
    """Guide an owner through current finances and optional tracked-data import."""

    plan = _owned_plan(request, plan_id)
    draft = get_object_or_404(FreedomScenario, plan=plan, status=FreedomScenario.Status.DRAFT)
    money_form = MoneyStepForm(request.POST or None, instance=draft)
    actuals_form, actuals_snapshot = _actuals_form_from_query(request)
    if request.method == "POST" and request.POST.get("action") == "load_actuals":
        actuals_form = ActualsSnapshotForm(request.POST, user=request.user)
        actuals_snapshot = _snapshot_from_form(request, actuals_form)
        if actuals_snapshot:
            base_values = money_form.scenario_values() if money_form.is_valid() else {}
            money_form = MoneyStepForm(
                instance=draft,
                initial={**base_values, **actuals_snapshot.scenario_values()},
            )
    elif request.method == "GET" and actuals_snapshot:
        money_form = MoneyStepForm(instance=draft, initial=actuals_snapshot.scenario_values())
    elif request.method == "POST" and money_form.is_valid():
        FreedomScenarioService().update_draft(
            user=request.user,
            draft=draft,
            scenario_data=money_form.scenario_values(),
        )
        messages.success(request, "Your current money picture is saved.")
        return redirect("freedom_plan_events", plan_id=plan.pk)
    return render(
        request,
        "financial_planning/wizard_money.html",
        {
            "plan": plan,
            "draft": draft,
            "money_form": money_form,
            "actuals_form": actuals_form,
            "actuals_snapshot": actuals_snapshot,
            "current_step": 2,
            "wizard_steps": WIZARD_STEPS,
            "selected_tab": "financial_freedom",
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def plan_events_view(request: TypedHttpRequest, plan_id: int) -> HttpResponse:
    """Collect optional future plans without requiring investing terminology."""

    plan = _owned_plan(request, plan_id)
    draft = get_object_or_404(FreedomScenario, plan=plan, status=FreedomScenario.Status.DRAFT)
    event_formset = _event_formset(request, draft=draft)
    if request.method == "POST" and request.POST.get("action") == "skip_events":
        messages.info(request, "No life events added. You can return to this step later.")
        return redirect("freedom_plan_review", plan_id=plan.pk)
    if request.method == "POST" and event_formset.is_valid():
        FreedomScenarioService().update_draft(
            user=request.user,
            draft=draft,
            scenario_data={},
            events=event_formset_values(event_formset),
        )
        messages.success(request, "Your future plans are included in the estimate.")
        return redirect("freedom_plan_review", plan_id=plan.pk)
    return render(
        request,
        "financial_planning/wizard_events.html",
        {
            "plan": plan,
            "draft": draft,
            "event_formset": event_formset,
            "event_presets": EVENT_PRESETS,
            "current_step": 3,
            "wizard_steps": WIZARD_STEPS,
            "selected_tab": "financial_freedom",
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def plan_review_view(request: TypedHttpRequest, plan_id: int) -> HttpResponse:
    """Explain the estimate and keep technical assumptions optional."""

    plan = _owned_plan(request, plan_id)
    draft = get_object_or_404(
        FreedomScenario.objects.prefetch_related("events"),
        plan=plan,
        status=FreedomScenario.Status.DRAFT,
    )
    review_form = ReviewStepForm(request.POST or None, instance=draft)
    if request.method == "POST" and review_form.is_valid():
        FreedomScenarioService().update_draft(
            user=request.user,
            draft=draft,
            scenario_data=review_form.scenario_values(),
        )
        messages.success(request, "Estimate updated with your assumptions.")
        return redirect("freedom_plan_review", plan_id=plan.pk)
    return render(
        request,
        "financial_planning/wizard_review.html",
        _review_context(plan=plan, draft=draft, review_form=review_form),
    )


@login_required
@require_POST
def calculate_preview_view(request: TypedHttpRequest) -> JsonResponse:
    """Return an owned, server-authoritative preview for review-page assumptions."""

    try:
        plan_id = int(request.POST.get("plan_id", ""))
    except ValueError:
        return JsonResponse({"ok": False, "errors": {"plan_id": ["Invalid plan."]}}, status=400)
    plan = _owned_plan(request, plan_id)
    draft = get_object_or_404(
        FreedomScenario.objects.prefetch_related("events"),
        plan=plan,
        status=FreedomScenario.Status.DRAFT,
    )
    form = ReviewStepForm(request.POST, instance=draft)
    request_id = request.POST.get("preview_request_id", "")[:64]
    if not form.is_valid():
        return JsonResponse(
            {"ok": False, "request_id": request_id, "errors": {"scenario": form.errors.get_json_data()}},
            status=400,
        )
    result = FreedomScenarioService().calculate_preview(
        scenario_data=_preview_scenario_values(draft, form.scenario_values()),
        events=_preview_event_values(draft),
    )
    payload = cast(dict[str, Any], result.to_payload())
    base_case = payload["cases"]["base"]
    return JsonResponse(
        {
            "ok": True,
            "request_id": request_id,
            "schema_version": payload["schema_version"],
            "calculation_version": payload["calculation_version"],
            "summary": {
                "base_freedom_number": payload["target_breakdown"]["base_freedom_number_today"],
                "total_target": payload["target_breakdown"]["total_target"],
                "required_monthly_investment": base_case["required_contribution"]["monthly_contribution"],
                "projected_achievement_date": base_case["achievement"]["date"],
                "funding_gap": payload["funding_gap"],
                "progress_percent": payload["progress_percent"],
                "status": payload["status"],
            },
            "timeline": [
                {
                    "date": row["date"],
                    "closing_balance": row["closing_balance"],
                    "target": row["target"],
                    "event_outflow": row["event_outflow"],
                }
                for row in payload["monthly"]
            ],
            "separate_savings": payload["separate_savings"],
            "warnings": payload["warnings"],
        }
    )


@login_required
@require_http_methods(["GET", "POST"])
def plan_draft_view(request: TypedHttpRequest, plan_id: int) -> HttpResponse:
    """Display or recalculate the single editable draft for an owned plan."""

    plan = _owned_plan(request, plan_id)
    draft = get_object_or_404(FreedomScenario, plan=plan, status=FreedomScenario.Status.DRAFT)
    form = FreedomScenarioForm(request.POST or None, instance=draft)
    if request.method == "POST" and form.is_valid():
        draft = FreedomScenarioService().update_draft(
            user=request.user, draft=draft, scenario_data=form.scenario_values()
        )
        messages.success(request, "Estimate recalculated from your current inputs.")
        return redirect("freedom_plan_draft", plan_id=plan.pk)
    return render(
        request,
        "financial_planning/plan_builder.html",
        _builder_context(plan=plan, draft=draft, form=form, plan_form=None),
    )


@login_required
@require_POST
def plan_save_view(request: TypedHttpRequest, plan_id: int) -> HttpResponse:
    """Validate posted inputs, recalculate server-side, and save an immutable version."""

    plan = _owned_plan(request, plan_id)
    draft = get_object_or_404(FreedomScenario, plan=plan, status=FreedomScenario.Status.DRAFT)
    if "target_date" in request.POST:
        form = FreedomScenarioForm(request.POST, instance=draft)
        if not form.is_valid():
            return render(
                request,
                "financial_planning/plan_builder.html",
                _builder_context(plan=plan, draft=draft, form=form, plan_form=None),
                status=400,
            )
        draft = FreedomScenarioService().update_draft(
            user=request.user, draft=draft, scenario_data=form.scenario_values()
        )
    scenario = FreedomScenarioService().save_draft(user=request.user, draft=draft)
    messages.success(request, f"Saved immutable scenario version {scenario.version}.")
    return redirect("freedom_scenario_detail", plan_id=plan.pk, scenario_id=scenario.pk)


@login_required
@require_GET
def scenario_detail_view(request: TypedHttpRequest, plan_id: int, scenario_id: int) -> HttpResponse:
    """Render stored outputs for an owned immutable scenario."""

    scenario = _owned_scenario(request, plan_id, scenario_id)
    if scenario.status != FreedomScenario.Status.SAVED:
        return redirect("freedom_plan_draft", plan_id=plan_id)
    versions = list(
        FreedomScenario.objects.filter(plan=scenario.plan, status=FreedomScenario.Status.SAVED).order_by("-version")
    )
    context: dict[str, Any] = {
        "plan": scenario.plan,
        "scenario": scenario,
        "versions": versions,
        "has_draft": FreedomScenario.objects.filter(plan=scenario.plan, status=FreedomScenario.Status.DRAFT).exists(),
        "selected_tab": "financial_freedom",
    }
    context.update(scenario_result_context(scenario))
    chart_data = timeline_chart_payload(scenario)
    context["chart_data"] = chart_data
    context["chart_summary"] = timeline_summary(scenario, chart_data)
    return render(request, "financial_planning/scenario_detail.html", context)


@login_required
@require_GET
def scenario_compare_view(request: TypedHttpRequest, plan_id: int) -> HttpResponse:
    """Place two saved versions of one owned plan side by side without changing either."""

    plan = _owned_plan(request, plan_id)
    saved = list(FreedomScenario.objects.filter(plan=plan, status=FreedomScenario.Status.SAVED).order_by("-version"))
    form = ScenarioCompareForm(request.GET or None, plan=plan)
    context: dict[str, Any] = {
        "plan": plan,
        "form": form,
        "saved_versions": saved,
        "comparison": None,
        "selected_tab": "financial_freedom",
    }
    if request.GET and form.is_valid():
        older, newer = form.ordered_pair()
        context["comparison"] = compare_scenarios(older, newer)
    return render(request, "financial_planning/scenario_compare.html", context)


@login_required
@require_POST
def scenario_update_view(request: TypedHttpRequest, plan_id: int, scenario_id: int) -> HttpResponse:
    """Clone an owned saved scenario into the plan's new version-zero draft."""

    scenario = _owned_scenario(request, plan_id, scenario_id)
    try:
        draft = FreedomScenarioService().clone_to_draft(user=request.user, scenario=scenario)
    except DraftAlreadyExistsError:
        messages.info(request, "This plan already has an editable draft.")
        return redirect("freedom_plan_draft", plan_id=plan_id)
    messages.success(request, f"Created an editable draft based on version {scenario.version}.")
    return redirect("freedom_plan_review", plan_id=draft.plan_id)


@login_required
@require_POST
def scenario_duplicate_view(request: TypedHttpRequest, plan_id: int, scenario_id: int) -> HttpResponse:
    """Duplicate an owned saved scenario into a new editable plan."""

    scenario = _owned_scenario(request, plan_id, scenario_id)
    name = request.POST.get("name", "").strip() or None
    draft = FreedomScenarioService().duplicate_to_new_plan(user=request.user, scenario=scenario, name=name)
    messages.success(request, "Scenario duplicated into a new editable plan.")
    return redirect("freedom_plan_review", plan_id=draft.plan_id)


@login_required
@require_POST
def plan_rename_view(request: TypedHttpRequest, plan_id: int) -> HttpResponse:
    """Rename one owned plan using validated plan input."""

    plan = _owned_plan(request, plan_id)
    form = FreedomPlanForm(request.POST, instance=plan)
    if form.is_valid():
        FreedomScenarioService().rename_plan(user=request.user, plan=plan, name=form.cleaned_data["name"])
        messages.success(request, "Plan renamed.")
    else:
        messages.error(request, "Enter a valid plan name.")
    return redirect("freedom_plan_list")


@login_required
@require_POST
def plan_archive_view(request: TypedHttpRequest, plan_id: int) -> HttpResponse:
    """Archive one owned plan."""

    FreedomScenarioService().set_archived(user=request.user, plan=_owned_plan(request, plan_id), archived=True)
    messages.success(request, "Plan archived.")
    return redirect("freedom_plan_list")


@login_required
@require_POST
def plan_restore_view(request: TypedHttpRequest, plan_id: int) -> HttpResponse:
    """Restore one owned archived plan."""

    FreedomScenarioService().set_archived(user=request.user, plan=_owned_plan(request, plan_id), archived=False)
    messages.success(request, "Plan restored.")
    return redirect("freedom_plan_list")
