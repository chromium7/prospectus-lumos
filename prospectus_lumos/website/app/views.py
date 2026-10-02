import re

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from prospectus_lumos.apps.transactions.models import Transaction
from prospectus_lumos.core.utils import TypedHttpRequest, safe_return_url

from .forms import TransactionForm
from .utils import recent_transaction_categories


MONTH_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


@login_required(login_url="login")
def app_home_view(request: TypedHttpRequest) -> HttpResponse:
    """Render the budgeting dashboard for a validated calendar month."""
    requested_month = request.GET.get("month", "")
    current_month = timezone.localdate().strftime("%Y-%m")
    month = requested_month or current_month
    month_error = ""
    status = 200
    if not MONTH_PATTERN.fullmatch(month):
        month = current_month
        month_error = "Choose a month using the YYYY-MM format."
        status = 400

    return render(
        request,
        "app/home.html",
        {
            "dashboard": None,
            "month": month,
            "month_error": month_error,
            "selected_tab": "app_home",
        },
        status=status,
    )


@login_required(login_url="login")
def transaction_create_view(request: TypedHttpRequest) -> HttpResponse:
    """Render and process a server-side manual transaction form."""

    fallback_url = reverse("app:home")
    if request.method == "POST":
        form = TransactionForm(request.POST, user=request.user)
        if form.is_valid():
            saved = form.save()
            messages.success(request, f"{saved.get_transaction_type_display()} saved.")
            if request.POST.get("save_action") == "save_another":
                return redirect("app:transaction_create")
            return redirect(safe_return_url(request, fallback=fallback_url))
    else:
        form = TransactionForm(
            user=request.user,
            initial={
                "transaction_type": Transaction.TransactionType.EXPENSE,
                "date": timezone.localdate(),
            },
        )

    return render(
        request,
        "app/transaction_form.html",
        {
            "form": form,
            "next": safe_return_url(request, fallback=fallback_url),
            "recent_categories": recent_transaction_categories(request.user),
            "selected_tab": "app_activity",
        },
        status=400 if request.method == "POST" else 200,
    )
