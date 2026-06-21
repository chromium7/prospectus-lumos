from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import redirect, render

from prospectus_lumos.apps.ai_analysis.models import AISettings
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
