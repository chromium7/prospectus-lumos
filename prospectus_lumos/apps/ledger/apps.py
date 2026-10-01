from django.apps import AppConfig


class LedgerConfig(AppConfig):
    """Application configuration for the directly entered budgeting ledger."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "prospectus_lumos.apps.ledger"
    verbose_name = "Ledger"
