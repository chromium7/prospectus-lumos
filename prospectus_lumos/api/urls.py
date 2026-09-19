from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView

from django.conf import settings
from django.urls import path

from .views import Ping

app_name = "api"

urlpatterns = [
    path("ping", Ping.as_view(), name="ping"),
    # The published schema. It moves under /api/v1/ with the rest of the API
    # once the versioned base layer lands.
    path("schema", SpectacularAPIView.as_view(), name="schema"),
]

if settings.DEBUG:
    # Browsable docs are development-only; production publishes the schema
    # document alone.
    urlpatterns += [
        path("schema/docs", SpectacularRedocView.as_view(url_name="api:schema"), name="docs"),
    ]
