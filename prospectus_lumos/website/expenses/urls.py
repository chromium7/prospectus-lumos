from django.urls import path
from . import views

urlpatterns = [
    # Authentication
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    # Main pages
    path("", views.dashboard_view, name="dashboard"),
    path("documents/", views.document_list_view, name="document_list"),
    path("income-analyzer/", views.income_analyzer_view, name="income_analyzer"),
    path("expense-analyzer/", views.expense_analyzer_view, name="expense_analyzer"),
    path("category-analyzer/", views.category_analyzer_view, name="category_analyzer"),
    # Actions
    path("sync-documents/", views.sync_documents_view, name="sync_documents"),
    path("download-csv/<int:document_id>/", views.download_csv_view, name="download_csv"),
]
