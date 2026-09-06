from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from prospectus_lumos.apps.financial_planning.models import FreedomPlan, FreedomScenario
from prospectus_lumos.apps.financial_planning.services import (
    DraftAlreadyExistsError,
    FreedomScenarioService,
)
from prospectus_lumos.core.utils import TypedHttpRequest

from .forms import FreedomPlanForm, FreedomScenarioForm


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
    """Create a manual plan and calculated editable draft."""

    plan_form = FreedomPlanForm(request.POST or None)
    scenario_form = FreedomScenarioForm(request.POST or None, initial=FreedomScenarioForm.initial_values())
    if request.method == "POST" and plan_form.is_valid() and scenario_form.is_valid():
        draft = FreedomScenarioService().create_plan_with_draft(
            user=request.user,
            plan_data=plan_form.cleaned_data,
            scenario_data=scenario_form.scenario_values(),
        )
        messages.success(request, "Plan created. Review the estimate, then save when you are ready.")
        return redirect("freedom_plan_draft", plan_id=draft.plan_id)
    return render(
        request,
        "financial_planning/plan_builder.html",
        _builder_context(plan=None, draft=None, form=scenario_form, plan_form=plan_form),
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
