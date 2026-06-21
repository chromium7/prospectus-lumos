from __future__ import annotations

from django.db import models
from django.contrib.auth.models import User


class AISettings(models.Model):
    """AI analysis configuration for a user."""

    class Model(models.TextChoices):
        GEMINI_25_PRO = "gemini-2.5-pro", "Gemini 2.5 Pro"
        GEMINI_25_FLASH = "gemini-2.5-flash", "Gemini 2.5 Flash"
        GEMINI_20_FLASH = "gemini-2.0-flash", "Gemini 2.0 Flash"
        GEMINI_15_PRO = "gemini-1.5-pro", "Gemini 1.5 Pro"
        GEMINI_15_FLASH = "gemini-1.5-flash", "Gemini 1.5 Flash"

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="ai_settings")
    api_token = models.CharField(max_length=255, help_text="Google Gemini API key from Google AI Studio")
    model = models.CharField(max_length=100, choices=Model.choices, default=Model.GEMINI_25_FLASH)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "AI Settings"
        verbose_name_plural = "AI Settings"

    def __str__(self) -> str:
        return f"{self.user.username} - AI Settings"
