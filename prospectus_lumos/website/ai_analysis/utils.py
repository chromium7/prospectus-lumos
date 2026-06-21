from __future__ import annotations

from django.contrib import messages

from prospectus_lumos.apps.ai_analysis.models import AISettings
from prospectus_lumos.apps.ai_analysis.services import get_ai_insights, prepare_data_as_text
from prospectus_lumos.apps.documents.models import Document
from prospectus_lumos.core.constants import MONTHS_LIST
from prospectus_lumos.core.utils import TypedHttpRequest


def get_ai_settings_or_redirect(request: TypedHttpRequest) -> AISettings | None:
    """Return the user's AI settings or redirect with an error message."""
    ai_settings = AISettings.objects.filter(user=request.user).first()
    if not ai_settings:
        messages.error(request, "Please configure your AI settings before running analysis.")
        return None
    return ai_settings


def build_ai_analysis_context(
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
