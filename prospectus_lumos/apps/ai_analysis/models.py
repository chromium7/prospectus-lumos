from __future__ import annotations

from django.db import models
from django.contrib.auth.models import User


class AISettings(models.Model):
    """AI analysis configuration for a user."""

    class Model(models.TextChoices):
        GPT_4O = "gpt-4o", "GPT-4o"
        GPT_4O_MINI = "gpt-4o-mini", "GPT-4o Mini"
        GPT_4_TURBO = "gpt-4-turbo", "GPT-4 Turbo"
        GPT_4 = "gpt-4", "GPT-4"
        GPT_35_TURBO = "gpt-3.5-turbo", "GPT-3.5 Turbo"
        CLAUDE_3_OPUS = "claude-3-opus-20240229", "Claude 3 Opus"
        CLAUDE_3_SONNET = "claude-3-sonnet-20240229", "Claude 3 Sonnet"
        CLAUDE_3_HAIKU = "claude-3-haiku-20240307", "Claude 3 Haiku"

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="ai_settings")
    api_token = models.CharField(max_length=255, help_text="OpenAI or Anthropic API key")
    model = models.CharField(max_length=100, choices=Model.choices, default=Model.GPT_4O)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "AI Settings"
        verbose_name_plural = "AI Settings"

    def __str__(self) -> str:
        return f"{self.user.username} - AI Settings"
