from django.urls import path
from . import views

urlpatterns = [
    path("ai-settings/", views.ai_settings_view, name="ai_settings"),
]
