from django.urls import path

from . import views

urlpatterns = [
    # Google Drive credentials
    path("google/credentials/", views.google_credentials_view, name="google_credentials"),
    # Document sources
    path("sources/", views.source_list_view, name="source_list"),
    path("sources/add/", views.source_create_view, name="source_create"),
    path("sources/<int:pk>/edit/", views.source_update_view, name="source_update"),
    path("sources/<int:pk>/delete/", views.source_delete_view, name="source_delete"),
]
