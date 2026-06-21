from __future__ import annotations

from django import forms

from prospectus_lumos.apps.ai_analysis.models import AISettings


class AISettingsForm(forms.ModelForm):
    class Meta:
        model = AISettings
        fields = ["api_token", "model"]
        widgets = {
            "api_token": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "type": "password",
                    "placeholder": "sk-...",
                }
            ),
            "model": forms.Select(attrs={"class": "form-select"}),
        }
