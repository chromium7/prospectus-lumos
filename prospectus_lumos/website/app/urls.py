from django.urls import path

from . import views

app_name = "app"

urlpatterns = [
    path("", views.app_home_view, name="home"),
    path("transactions/new", views.transaction_create_view, name="transaction_create"),
]
