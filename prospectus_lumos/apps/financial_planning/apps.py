from django.apps import AppConfig


class FinancialPlanningConfig(AppConfig):
    """Application configuration for persisted financial-freedom plans."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "prospectus_lumos.apps.financial_planning"
    verbose_name = "Financial Planning"
