from django.urls import path
from . import views

urlpatterns = [
    path("ai-settings/", views.ai_settings_view, name="ai_settings"),
    path("income/", views.ai_income_analyzer_view, name="ai_income_analyzer"),
    path("expense/", views.ai_expense_analyzer_view, name="ai_expense_analyzer"),
    path("portfolio/", views.ai_portfolio_analyzer_view, name="ai_portfolio_analyzer"),
]
