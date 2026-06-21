from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import redirect, render

from prospectus_lumos.apps.ai_analysis.models import AISettings
from prospectus_lumos.apps.ai_analysis.services import get_ai_insights, prepare_data_as_text
from prospectus_lumos.apps.documents.models import Document
from prospectus_lumos.core.constants import MONTHS_LIST
from prospectus_lumos.core.utils import TypedHttpRequest

from .forms import AISettingsForm


@login_required
def ai_settings_view(request: TypedHttpRequest) -> HttpResponse:
    """View for managing AI API settings."""
    ai_settings = AISettings.objects.filter(user=request.user).first()

    if request.method == "POST":
        form = AISettingsForm(request.POST, instance=ai_settings)
        if form.is_valid():
            settings_obj = form.save(commit=False)
            settings_obj.user = request.user
            settings_obj.save()
            messages.success(request, "AI settings updated successfully")
            return redirect("ai_settings")
    else:
        form = AISettingsForm(instance=ai_settings)

    return render(
        request,
        "ai_analysis/settings.html",
        {"form": form, "selected_tab": "ai_settings", "ai_settings": ai_settings},
    )


def _get_ai_settings_or_redirect(request: TypedHttpRequest) -> AISettings | None:
    """Return the user's AI settings or redirect with an error message."""
    ai_settings = AISettings.objects.filter(user=request.user).first()
    if not ai_settings:
        messages.error(request, "Please configure your AI settings before running analysis.")
        return None
    return ai_settings


def _build_ai_analysis_context(
    request: TypedHttpRequest,
    ai_settings: AISettings,
    analyzer_type: str,
    year: int | None,
    month: int | None,
    year_from: int | None = None,
    year_to: int | None = None,
) -> dict:
    """Build the common context for AI analysis views."""
    documents_qs = Document.objects.filter(user=request.user).prefetch_related("transactions")

    if year is not None:
        documents_qs = documents_qs.filter(year=year)
    if month is not None:
        documents_qs = documents_qs.filter(month=month)
    if year_from is not None:
        documents_qs = documents_qs.filter(year__gte=year_from)
    if year_to is not None:
        documents_qs = documents_qs.filter(year__lte=year_to)

    documents = list(documents_qs.order_by("year", "month"))

    insights: str | None = None
    error: str | None = None

    if documents:
        data_text = prepare_data_as_text(documents, analyzer_type)
        if data_text.strip():
            try:
                insights = get_ai_insights(
                    api_token=ai_settings.api_token,
                    model=ai_settings.model,
                    data_text=data_text,
                )
            except Exception as e:
                error = str(e)
        else:
            error = f"No {analyzer_type} data found for the selected period."
    else:
        error = "No documents found for the selected period."

    available_years = (
        Document.objects.filter(user=request.user).values_list("year", flat=True).distinct().order_by("-year")
    )
    available_months = MONTHS_LIST

    return {
        "insights": insights,
        "error": error,
        "available_years": available_years,
        "available_months": available_months,
        "document_count": len(documents),
    }


@login_required
def ai_income_analyzer_view(request: TypedHttpRequest) -> HttpResponse:
    """AI-powered income analysis with period selection."""
    ai_settings = _get_ai_settings_or_redirect(request)
    if ai_settings is None:
        return redirect("ai_settings")

    year_filter = request.GET.get("year", "")
    month_filter = request.GET.get("month", "")

    year = int(year_filter) if year_filter else None
    month = int(month_filter) if month_filter else None

    context = {
        "selected_tab": "ai_income",
        "year_filter": year_filter,
        "month_filter": month_filter,
    }

    if year_filter or month_filter:
        filter_text = []
        if month is not None:
            filter_text.append(MONTHS_LIST[month - 1][1])
        if year is not None:
            filter_text.append(str(year))
        context["filter_text"] = " for " + " ".join(filter_text) if filter_text else ""
        context.update(_build_ai_analysis_context(request, ai_settings, "income", year=year, month=month))
    else:
        context["available_years"] = (
            Document.objects.filter(user=request.user).values_list("year", flat=True).distinct().order_by("-year")
        )
        context["available_months"] = MONTHS_LIST

    return render(request, "ai_analysis/income.html", context)


@login_required
def ai_expense_analyzer_view(request: TypedHttpRequest) -> HttpResponse:
    """AI-powered expense analysis with period selection."""
    ai_settings = _get_ai_settings_or_redirect(request)
    if ai_settings is None:
        return redirect("ai_settings")

    year_filter = request.GET.get("year", "")
    month_filter = request.GET.get("month", "")

    year = int(year_filter) if year_filter else None
    month = int(month_filter) if month_filter else None

    context = {
        "selected_tab": "ai_expenses",
        "year_filter": year_filter,
        "month_filter": month_filter,
    }

    if year_filter or month_filter:
        filter_text = []
        if month is not None:
            filter_text.append(MONTHS_LIST[month - 1][1])
        if year is not None:
            filter_text.append(str(year))
        context["filter_text"] = " for " + " ".join(filter_text) if filter_text else ""
        context.update(_build_ai_analysis_context(request, ai_settings, "expense", year=year, month=month))
    else:
        context["available_years"] = (
            Document.objects.filter(user=request.user).values_list("year", flat=True).distinct().order_by("-year")
        )
        context["available_months"] = MONTHS_LIST

    return render(request, "ai_analysis/expense.html", context)


@login_required
def ai_portfolio_analyzer_view(request: TypedHttpRequest) -> HttpResponse:
    """AI-powered portfolio analysis with year range selection."""
    ai_settings = _get_ai_settings_or_redirect(request)
    if ai_settings is None:
        return redirect("ai_settings")

    year_from_str = request.GET.get("year_from", "")
    year_to_str = request.GET.get("year_to", "")

    year_from = int(year_from_str) if year_from_str else None
    year_to = int(year_to_str) if year_to_str else None

    if year_from is not None and year_to is not None and year_from > year_to:
        year_from, year_to = year_to, year_from
        year_from_str, year_to_str = str(year_from), str(year_to)

    context = {
        "selected_tab": "ai_portfolio",
        "year_from": year_from_str,
        "year_to": year_to_str,
    }

    if year_from_str or year_to_str:
        parts = []
        if year_from is not None:
            parts.append(f"from {year_from}")
        if year_to is not None:
            parts.append(f"to {year_to}")
        context["filter_text"] = " " + " ".join(parts)
        context.update(
            _build_ai_analysis_context(
                request,
                ai_settings,
                "portfolio",
                year=None,
                month=None,
                year_from=year_from,
                year_to=year_to,
            )
        )
    else:
        context["available_years"] = (
            Document.objects.filter(user=request.user).values_list("year", flat=True).distinct().order_by("-year")
        )

    return render(request, "ai_analysis/portfolio.html", context)
