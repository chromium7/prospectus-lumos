from django.urls import path

from . import views

app_name = "app"

urlpatterns = [
    path("", views.app_home_view, name="home"),
]
