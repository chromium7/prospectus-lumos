from django.urls import include, path

urlpatterns = [
    path("", include("prospectus_lumos.website.expenses.urls")),
    path("accounts/", include("prospectus_lumos.website.accounts.urls")),
]
