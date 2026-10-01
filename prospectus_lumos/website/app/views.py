import re
from decimal import Decimal
from uuid import uuid4

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from prospectus_lumos.apps.ledger.models import Category, FinancialAccount, LedgerTransaction
from prospectus_lumos.apps.ledger.services import (
    active_transactions,
    category_actuals,
    ensure_default_categories,
    month_range,
    month_totals,
)
from prospectus_lumos.core.utils import TypedHttpRequest

from .forms import TransactionForm


MONTH_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
PENDING_TOKEN_KEY = "transaction_form_pending_tokens"
USED_TOKEN_KEY = "transaction_form_used_tokens"
TOKEN_LIMIT = 20


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


def _issue_submission_token(request: HttpRequest) -> str:
    """Keep a bounded set of form tokens so multiple open tabs remain valid."""

    token = uuid4().hex
    pending = list(request.session.get(PENDING_TOKEN_KEY, []))
    pending.append(token)
    request.session[PENDING_TOKEN_KEY] = pending[-TOKEN_LIMIT:]
    return token


def _consume_submission_token(request: HttpRequest, token: str) -> None:
    """Move a successfully used form token into the replay-protection set."""

    pending = list(request.session.get(PENDING_TOKEN_KEY, []))
    if token in pending:
        pending.remove(token)
    used = list(request.session.get(USED_TOKEN_KEY, []))
    used.append(token)
    request.session[PENDING_TOKEN_KEY] = pending
    request.session[USED_TOKEN_KEY] = used[-TOKEN_LIMIT:]


def _safe_return_url(request: HttpRequest) -> str:
    """Return a same-origin destination or the app dashboard."""

    candidate = request.POST.get("next", "") or request.GET.get("next", "")
    if candidate and url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return "/app/"


def _format_idr(amount: Decimal) -> str:
    """Format a whole-rupiah amount for a concise success announcement."""

    return f"Rp{amount:,.0f}".replace(",", ".")


def _success_message(transaction: LedgerTransaction) -> str:
    """Describe the saved result using the same ledger totals as reports."""

    start, end = month_range(transaction.occurred_on)
    if transaction.type == LedgerTransaction.Type.EXPENSE and transaction.category_id:
        actual = category_actuals(transaction.user, start, end).get(transaction.category_id, Decimal("0.00"))
        return f"Expense saved. {transaction.category.name} spending this month is now {_format_idr(actual)}."
    if transaction.type == LedgerTransaction.Type.INCOME:
        income = month_totals(transaction.user, start, end)["income"]
        return f"Income saved. Income this month is now {_format_idr(income)}."
    return "Transfer saved. Your account balances are updated."


@login_required(login_url="login")
def transaction_create_view(request: TypedHttpRequest) -> HttpResponse:
    """Render and process the server-side add-transaction workflow."""

    accounts = FinancialAccount.objects.for_user(request.user).active()
    has_accounts = accounts.exists()
    if has_accounts:
        ensure_default_categories(request.user)

    recent_accounts: list[FinancialAccount] = []
    recent_categories: list[Category] = []
    seen_account_ids: set[int] = set()
    seen_category_ids: set[int] = set()
    recent_transactions = active_transactions(request.user).select_related("account", "transfer_account", "category")[
        :12
    ]
    for transaction in recent_transactions:
        transaction_accounts = (transaction.account, transaction.transfer_account)
        for account in transaction_accounts:
            if account and account.archived_at is None and account.id not in seen_account_ids:
                recent_accounts.append(account)
                seen_account_ids.add(account.id)
        if (
            transaction.category
            and transaction.category.archived_at is None
            and transaction.category_id not in seen_category_ids
        ):
            recent_categories.append(transaction.category)
            seen_category_ids.add(transaction.category_id)
        if len(recent_accounts) >= 3 and len(recent_categories) >= 3:
            break

    if request.method == "POST":
        form = TransactionForm(request.POST, user=request.user)
        token = request.POST.get("submission_token", "")
        used_tokens = request.session.get(USED_TOKEN_KEY, [])
        pending_tokens = request.session.get(PENDING_TOKEN_KEY, [])

        if token in used_tokens:
            messages.info(request, "That transaction was already saved. No duplicate was created.")
            return redirect(_safe_return_url(request))
        if token not in pending_tokens:
            form.add_error(None, "This form has expired. Reload the page and try again.")
        elif form.is_valid():
            transaction = form.save()
            _consume_submission_token(request, token)
            messages.success(request, _success_message(transaction))
            if request.POST.get("save_action") == "save_another":
                return redirect("app:transaction_create")
            return redirect(_safe_return_url(request))
    else:
        form = TransactionForm(
            user=request.user,
            initial={
                "occurred_on": timezone.localdate(),
                "submission_token": _issue_submission_token(request),
                "type": LedgerTransaction.Type.EXPENSE,
            },
        )

    return render(
        request,
        "app/transaction_form.html",
        {
            "form": form,
            "has_accounts": has_accounts,
            "next": _safe_return_url(request),
            "recent_accounts": recent_accounts[:3],
            "recent_categories": recent_categories[:3],
            "selected_tab": "app_activity",
        },
        status=400 if request.method == "POST" and form.errors else 200,
    )
