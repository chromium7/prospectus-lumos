from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import render

from prospectus_lumos.core.utils import TypedHttpRequest


@login_required(login_url="login")
def app_home_view(request: TypedHttpRequest) -> HttpResponse:
    """Render the authenticated budgeting app shell."""
    return render(
        request,
        "app/home.html",
        {
            "active_navigation": "home",
            "app_page_eyebrow": "Your money",
            "app_page_title": "Budgeting home",
        },
    )
