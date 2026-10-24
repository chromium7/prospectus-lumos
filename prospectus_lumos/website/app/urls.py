from django.urls import path

from . import views
from .budgets import monthly_budgets_view

app_name = "app"

urlpatterns = [
    path("budgets/", monthly_budgets_view, name="budgets"),
    path("", views.app_home_view, name="home"),
    path("transactions/new", views.transaction_create_view, name="transaction_create"),
    path("transactions/<int:transaction_id>/edit", views.transaction_edit_view, name="transaction_edit"),
]
