import logging
import re
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import InterfaceError, OperationalError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from prospectus_lumos.apps.transactions.models import Transaction
from prospectus_lumos.apps.transactions.services import ImportedTransactionEditError, TransactionEditConflict
from prospectus_lumos.core.utils import TypedHttpRequest, safe_return_url

from .forms import TransactionForm
from .utils import recent_transaction_categories


logger = logging.getLogger(__name__)

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
    return_url = safe_return_url(request, fallback=fallback_url)
    request_failed = False
    if request.method == "POST":
        form = TransactionForm(request.POST, user=request.user)
        if form.is_valid():
            try:
                saved = form.save()
            except (InterfaceError, OperationalError):
                logger.exception("Manual transaction save failed for user %s", request.user.pk)
                request_failed = True
                form.add_error(
                    None,
                    "We couldn't save this transaction. Your entries are still here; please try again.",
                )
            else:
                messages.success(request, f"{saved.get_transaction_type_display()} saved.")
                if request.POST.get("save_action") == "save_another":
                    if return_url == fallback_url:
                        return redirect("app:transaction_create")
                    query = urlencode({"next": return_url})
                    return redirect(f"{reverse('app:transaction_create')}?{query}")
                return redirect(return_url)
    else:
        form = TransactionForm(
            user=request.user,
            initial={
                "transaction_type": Transaction.TransactionType.EXPENSE,
                "date": timezone.localdate(),
            },
        )

    recent_categories = [] if request_failed else recent_transaction_categories(request.user)
    return render(
        request,
        "app/transaction_form.html",
        {
            "form": form,
            "page_mode": "create",
            "next": return_url,
            "recent_categories": recent_categories,
            "selected_tab": "app_add",
        },
        status=503 if request_failed else 400 if request.method == "POST" else 200,
    )


@login_required(login_url="login")
def transaction_edit_view(request: TypedHttpRequest, transaction_id: int) -> HttpResponse:
    """Render an owner-scoped editor or source-managed read-only transaction state."""

    owned_transactions = Transaction.for_user(request.user).select_related("document", "document__source")
    editable_transaction = get_object_or_404(owned_transactions, pk=transaction_id)
    fallback_url = reverse("app:home")
    if editable_transaction.document is not None:
        document_month = f"{editable_transaction.document.year}-{editable_transaction.document.month:02d}"
        fallback_url = f"{fallback_url}?{urlencode({'month': document_month})}"
    return_url = safe_return_url(request, fallback=fallback_url)
    is_editable = editable_transaction.origin == Transaction.Origin.MANUAL

    if not is_editable:
        if request.method == "POST":
            messages.error(
                request,
                "This imported transaction is read-only here. Update it through its source workflow.",
            )
            query = urlencode({"next": return_url})
            return redirect(f"{reverse('app:transaction_edit', args=[editable_transaction.pk])}?{query}")

        return render(
            request,
            "app/transaction_form.html",
            {
                "form": None,
                "is_editable": False,
                "next": return_url,
                "page_mode": "edit",
                "recent_categories": [],
                "selected_tab": "",
                "transaction": editable_transaction,
            },
        )

    request_failed = False
    edit_conflict = False
    if request.method == "POST":
        form = TransactionForm(request.POST, user=request.user, instance=editable_transaction)
        if form.is_valid():
            try:
                saved = form.save()
            except (ImportedTransactionEditError, TransactionEditConflict) as error:
                edit_conflict = True
                form.add_error(None, str(error))
            except (InterfaceError, OperationalError):
                logger.exception("Manual transaction edit failed for user %s", request.user.pk)
                request_failed = True
                form.add_error(
                    None,
                    "We couldn't update this transaction. Your entries are still here; please try again.",
                )
            else:
                messages.success(request, f"{saved.get_transaction_type_display()} updated.")
                return redirect(return_url)
    else:
        form = TransactionForm(user=request.user, instance=editable_transaction)

    recent_categories = [] if request_failed else recent_transaction_categories(request.user)
    return render(
        request,
        "app/transaction_form.html",
        {
            "form": form,
            "is_editable": True,
            "next": return_url,
            "page_mode": "edit",
            "recent_categories": recent_categories,
            "selected_tab": "",
            "transaction": editable_transaction,
        },
        status=503 if request_failed else 409 if edit_conflict else 400 if request.method == "POST" else 200,
    )
