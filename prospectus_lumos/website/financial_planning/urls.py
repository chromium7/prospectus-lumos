from django.urls import path

from . import views

urlpatterns = [
    path("", views.plan_list_view, name="freedom_plan_list"),
    path("new/", views.plan_create_view, name="freedom_plan_create"),
    path("<int:plan_id>/draft/", views.plan_draft_view, name="freedom_plan_draft"),
    path("<int:plan_id>/save/", views.plan_save_view, name="freedom_plan_save"),
    path(
        "<int:plan_id>/scenarios/<int:scenario_id>/",
        views.scenario_detail_view,
        name="freedom_scenario_detail",
    ),
    path(
        "<int:plan_id>/scenarios/<int:scenario_id>/update/",
        views.scenario_update_view,
        name="freedom_scenario_update",
    ),
    path(
        "<int:plan_id>/scenarios/<int:scenario_id>/duplicate/",
        views.scenario_duplicate_view,
        name="freedom_scenario_duplicate",
    ),
    path("<int:plan_id>/rename/", views.plan_rename_view, name="freedom_plan_rename"),
    path("<int:plan_id>/archive/", views.plan_archive_view, name="freedom_plan_archive"),
    path("<int:plan_id>/restore/", views.plan_restore_view, name="freedom_plan_restore"),
]
