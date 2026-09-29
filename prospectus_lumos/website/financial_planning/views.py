from __future__ import annotations

from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch
from django.http import HttpResponse
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

from .forms import ActualsSnapshotForm, FreedomPlanForm, FreedomScenarioForm, GoalStepForm, MoneyStepForm

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
    scenarios = FreedomScenario.objects.order_by("-version", "-created_at")
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
        setattr(
            plan,
            "library_latest",
            next(
                (scenario for scenario in plan.library_scenarios if scenario.status == FreedomScenario.Status.SAVED),
                None,
            ),
        )
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
        return redirect("freedom_plan_draft", plan_id=plan.pk)
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
    if request.POST:
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
    return render(
        request,
        "financial_planning/scenario_detail.html",
        {"plan": scenario.plan, "scenario": scenario, "selected_tab": "financial_freedom"},
    )


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
    return redirect("freedom_plan_draft", plan_id=draft.plan_id)


@login_required
@require_POST
def scenario_duplicate_view(request: TypedHttpRequest, plan_id: int, scenario_id: int) -> HttpResponse:
    """Duplicate an owned saved scenario into a new editable plan."""

    scenario = _owned_scenario(request, plan_id, scenario_id)
    name = request.POST.get("name", "").strip() or None
    draft = FreedomScenarioService().duplicate_to_new_plan(user=request.user, scenario=scenario, name=name)
    messages.success(request, "Scenario duplicated into a new editable plan.")
    return redirect("freedom_plan_draft", plan_id=draft.plan_id)


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
