from __future__ import annotations

from typing import Any, cast

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
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
    event_formset_values,
)


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
    event_formset: BaseFinancialEventFormSet,
    actuals_form: ActualsSnapshotForm,
    actuals_snapshot: ActualsSnapshot | None = None,
) -> dict[str, object]:
    return {
        "plan": plan,
        "draft": draft,
        "form": form,
        "plan_form": plan_form,
        "event_formset": event_formset,
        "event_presets": EVENT_PRESETS,
        "actuals_form": actuals_form,
        "actuals_snapshot": actuals_snapshot,
        "selected_tab": "financial_freedom",
    }


def _event_formset(
    request: HttpRequest,
    *,
    instance: FreedomScenario | None,
    calculation_date: object = None,
) -> tuple[BaseFinancialEventFormSet, bool]:
    submitted = request.method == "POST" and "events-TOTAL_FORMS" in request.POST
    formset = cast(
        BaseFinancialEventFormSet,
        FinancialEventFormSet(
            request.POST if submitted else None,
            instance=instance or FreedomScenario(),
            prefix="events",
            calculation_date=calculation_date,
        ),
    )
    return formset, submitted


def _snapshot_from_form(request: TypedHttpRequest, form: ActualsSnapshotForm) -> ActualsSnapshot | None:
    if not form.is_valid():
        return None
    return ActualsSnapshotService(user=request.user).create_snapshot(**form.snapshot_options())


def _snapshot_initial(snapshot: ActualsSnapshot, *, base: dict[str, object] | None = None) -> dict[str, object]:
    return {**(base or {}), **snapshot.scenario_values()}


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
    actuals_form, actuals_snapshot = _actuals_form_from_query(request)
    initial = FreedomScenarioForm.initial_values()
    if actuals_snapshot:
        initial = _snapshot_initial(actuals_snapshot, base=initial)
    scenario_form = FreedomScenarioForm(request.POST or None, initial=initial)
    if request.method == "POST" and request.POST.get("action") == "load_actuals":
        actuals_form = ActualsSnapshotForm(request.POST, user=request.user)
        actuals_snapshot = _snapshot_from_form(request, actuals_form)
        if actuals_snapshot:
            current_values = scenario_form.scenario_values() if scenario_form.is_valid() else initial
            scenario_form = FreedomScenarioForm(initial=_snapshot_initial(actuals_snapshot, base=current_values))
        event_formset, _ = _event_formset(request, instance=None)
        return render(
            request,
            "financial_planning/plan_builder.html",
            _builder_context(
                plan=None,
                draft=None,
                form=scenario_form,
                plan_form=plan_form,
                event_formset=event_formset,
                actuals_form=actuals_form,
                actuals_snapshot=actuals_snapshot,
            ),
        )
    scenario_valid = scenario_form.is_valid() if request.method == "POST" else False
    calculation_date = scenario_form.cleaned_data.get("calculation_date") if scenario_valid else None
    event_formset, events_submitted = _event_formset(request, instance=None, calculation_date=calculation_date)
    events_valid = event_formset.is_valid() if events_submitted else True
    if request.method == "POST" and plan_form.is_valid() and scenario_valid and events_valid:
        draft = FreedomScenarioService().create_plan_with_draft(
            user=request.user,
            plan_data=plan_form.cleaned_data,
            scenario_data=scenario_form.scenario_values(),
            events=event_formset_values(event_formset) if events_submitted else (),
        )
        messages.success(request, "Plan created. Review the estimate, then save when you are ready.")
        return redirect("freedom_plan_draft", plan_id=draft.plan_id)
    return render(
        request,
        "financial_planning/plan_builder.html",
        _builder_context(
            plan=None,
            draft=None,
            form=scenario_form,
            plan_form=plan_form,
            event_formset=event_formset,
            actuals_form=actuals_form,
            actuals_snapshot=actuals_snapshot,
        ),
    )


@login_required
@require_http_methods(["GET", "POST"])
def plan_draft_view(request: TypedHttpRequest, plan_id: int) -> HttpResponse:
    """Display or recalculate the single editable draft for an owned plan."""

    plan = _owned_plan(request, plan_id)
    draft = get_object_or_404(FreedomScenario, plan=plan, status=FreedomScenario.Status.DRAFT)
    form = FreedomScenarioForm(request.POST or None, instance=draft)
    actuals_form = ActualsSnapshotForm(
        request.POST if request.POST.get("action") == "load_actuals" else None,
        user=request.user,
    )
    actuals_snapshot = None
    if request.method == "POST" and request.POST.get("action") == "load_actuals":
        actuals_snapshot = _snapshot_from_form(request, actuals_form)
        if actuals_snapshot:
            current_values = form.scenario_values() if form.is_valid() else {}
            form = FreedomScenarioForm(
                instance=draft,
                initial=_snapshot_initial(actuals_snapshot, base=current_values),
            )
        event_formset, _ = _event_formset(request, instance=draft)
        return render(
            request,
            "financial_planning/plan_builder.html",
            _builder_context(
                plan=plan,
                draft=draft,
                form=form,
                plan_form=None,
                event_formset=event_formset,
                actuals_form=actuals_form,
                actuals_snapshot=actuals_snapshot,
            ),
        )
    form_valid = form.is_valid() if request.method == "POST" else False
    calculation_date = form.cleaned_data.get("calculation_date") if form_valid else draft.calculation_date
    event_formset, events_submitted = _event_formset(request, instance=draft, calculation_date=calculation_date)
    events_valid = event_formset.is_valid() if events_submitted else True
    if request.method == "POST" and form_valid and events_valid:
        draft = FreedomScenarioService().update_draft(
            user=request.user,
            draft=draft,
            scenario_data=form.scenario_values(),
            events=event_formset_values(event_formset) if events_submitted else None,
        )
        messages.success(request, "Estimate recalculated from your current inputs.")
        return redirect("freedom_plan_draft", plan_id=plan.pk)
    return render(
        request,
        "financial_planning/plan_builder.html",
        _builder_context(
            plan=plan,
            draft=draft,
            form=form,
            plan_form=None,
            event_formset=event_formset,
            actuals_form=actuals_form,
            actuals_snapshot=actuals_snapshot,
        ),
    )


@login_required
@require_POST
def plan_save_view(request: TypedHttpRequest, plan_id: int) -> HttpResponse:
    """Validate posted inputs, recalculate server-side, and save an immutable version."""

    plan = _owned_plan(request, plan_id)
    draft = get_object_or_404(FreedomScenario, plan=plan, status=FreedomScenario.Status.DRAFT)
    if request.POST:
        form = FreedomScenarioForm(request.POST, instance=draft)
        form_valid = form.is_valid()
        event_formset, events_submitted = _event_formset(
            request,
            instance=draft,
            calculation_date=form.cleaned_data.get("calculation_date") if form_valid else draft.calculation_date,
        )
        events_valid = event_formset.is_valid() if events_submitted else True
        if not form_valid or not events_valid:
            return render(
                request,
                "financial_planning/plan_builder.html",
                _builder_context(
                    plan=plan,
                    draft=draft,
                    form=form,
                    plan_form=None,
                    event_formset=event_formset,
                    actuals_form=ActualsSnapshotForm(user=request.user),
                ),
                status=400,
            )
        draft = FreedomScenarioService().update_draft(
            user=request.user,
            draft=draft,
            scenario_data=form.scenario_values(),
            events=event_formset_values(event_formset) if events_submitted else None,
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


@login_required
@require_POST
def calculate_preview_view(request: TypedHttpRequest) -> JsonResponse:
    """Return a server-authoritative transient scenario preview."""

    plan_id = request.POST.get("plan_id")
    draft = None
    if plan_id:
        try:
            normalized_plan_id = int(plan_id)
        except ValueError:
            return JsonResponse({"ok": False, "errors": {"plan_id": ["Invalid plan."]}}, status=400)
        plan = _owned_plan(request, normalized_plan_id)
        draft = get_object_or_404(FreedomScenario, plan=plan, status=FreedomScenario.Status.DRAFT)
    form = FreedomScenarioForm(request.POST, instance=draft)
    form_valid = form.is_valid()
    event_formset, events_submitted = _event_formset(
        request,
        instance=draft,
        calculation_date=form.cleaned_data.get("calculation_date") if form_valid else None,
    )
    events_valid = event_formset.is_valid() if events_submitted else True
    request_id = request.POST.get("preview_request_id", "")[:64]
    if not form_valid or not events_valid:
        return JsonResponse(
            {
                "ok": False,
                "request_id": request_id,
                "errors": {
                    "scenario": form.errors.get_json_data(),
                    "events": [errors.get_json_data() for errors in event_formset.errors],
                    "event_formset": list(event_formset.non_form_errors()),
                },
            },
            status=400,
        )
    result = FreedomScenarioService().calculate_preview(
        scenario_data=form.scenario_values(),
        events=event_formset_values(event_formset) if events_submitted else [],
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
@require_POST
def actuals_preview_view(request: TypedHttpRequest) -> JsonResponse:
    """Return a JSON-safe preview of an owned tracked-finance snapshot."""

    form = ActualsSnapshotForm(request.POST, user=request.user)
    snapshot = _snapshot_from_form(request, form)
    if snapshot is None:
        return JsonResponse({"ok": False, "errors": form.errors.get_json_data()}, status=400)
    return JsonResponse({"ok": True, "snapshot": snapshot.to_payload()})
