from django.urls import path

from .views import Ping

app_name = "api"

urlpatterns = [
    path("ping", Ping.as_view(), name="ping"),
]
