import logging
from datetime import date
from urllib.parse import urlencode

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import InterfaceError, OperationalError
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from prospectus_lumos.apps.transactions.budget_services import (
    get_monthly_budget_actuals,
    remove_category_budget,
    upsert_category_budget,
)
from prospectus_lumos.core.utils import TypedHttpRequest

logger = logging.getLogger(__name__)


class BudgetMonthForm(forms.Form):
    month = forms.DateField(
        input_formats=["%Y-%m"],
        widget=forms.DateInput(format="%Y-%m", attrs={"type": "month", "class": "form-control"}),
    )


class BudgetLimitForm(forms.Form):
    category = forms.CharField(max_length=100, widget=forms.TextInput(attrs={"class": "form-control"}))
    amount = forms.DecimalField(
        min_value=0,
        max_digits=15,
        decimal_places=2,
        label="Budget (IDR)",
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "inputmode": "decimal"}),
    )


@login_required(login_url="login")
@require_http_methods(["GET", "POST"])
def monthly_budgets_view(request: TypedHttpRequest) -> HttpResponse:
    """Display and edit the authenticated user's category limits for one month."""
    month_form = BudgetMonthForm(
        {
            "month": request.GET.get("month", timezone.localdate().strftime("%Y-%m")),
        }
    )
    if not month_form.is_valid():
        return render(
            request,
            "app/budgets.html",
            {
                "month_form": month_form,
                "selected_tab": "app_budgets",
                "invalid_month": True,
            },
            status=400,
        )
    period = month_form.cleaned_data["month"]
    month = f"{period.year:04d}-{period.month:02d}"
    url = f"{reverse('app:budgets')}?{urlencode({'month': month})}"
    form = BudgetLimitForm(prefix="new")
    submitted = None
    error = ""
    status = 200
    if request.method == "POST":
        submitted = BudgetLimitForm(request.POST, prefix=request.POST.get("prefix", "new"))
        action = request.POST.get("action")
        amount_field = submitted.fields["amount"]
        if action == "remove":
            # Removal needs only the category; the amount can be an unsaved edit.
            submitted.fields.pop("amount")
        if action not in {"save", "remove"}:
            submitted.add_error(None, "Choose Save limit or Remove limit.")
        valid = submitted.is_valid()
        submitted.fields["amount"] = amount_field
        if valid:
            try:
                values = {
                    "user": request.user,
                    "year": period.year,
                    "month": period.month,
                    "category": submitted.cleaned_data["category"],
                }
                if action == "remove":
                    remove_category_budget(**values)
                else:
                    upsert_category_budget(**values, amount=submitted.cleaned_data["amount"])
            except ValidationError as exc:
                submitted.add_error(None, " ".join(exc.messages))
            except (InterfaceError, OperationalError):
                logger.exception("Budget write failed for user %s", request.user.pk)
                error = "We couldn't save your changes. Your entries are still here; please try again."
                status = 503
            else:
                messages.success(request, "Budget limit removed." if action == "remove" else "Budget limit saved.")
                return redirect(url)
        if status == 200:
            status = 400
            error = "Please correct the budget entries below."
        if submitted.prefix == "new":
            form = submitted
    rows = []
    try:
        actuals = get_monthly_budget_actuals(user=request.user, year=period.year, month=period.month)
    except (InterfaceError, OperationalError):
        logger.exception("Budget read failed for user %s", request.user.pk)
        read_error = "We couldn't load this month's budgets. Please try again."
        error = f"{error} {read_error}" if error else read_error
        status = 503
    else:
        for index, row in enumerate(actuals):
            prefix = f"row-{index}"
            editor = BudgetLimitForm(prefix=prefix, initial={"category": row.category, "amount": row.budget})
            editor.fields["category"].widget = forms.HiddenInput()
            if submitted is not None and submitted.prefix == prefix:
                editor = submitted
                editor.fields["category"].widget = forms.HiddenInput()
            rows.append({"actual": row, "form": editor})
    if submitted is not None and submitted.prefix != "new" and not any(row["form"] is submitted for row in rows):
        # Keep an inline edit accessible if a read fails or its category disappears.
        submitted.fields["category"].widget = forms.TextInput(attrs={"class": "form-control"})
        form = submitted
    ordinal = period.year * 12 + period.month - 1

    def adjacent(offset: int) -> str | None:
        year, month_index = divmod(ordinal + offset, 12)
        return f"{year:04d}-{month_index + 1:02d}" if 1 <= year <= 9999 else None

    return render(
        request,
        "app/budgets.html",
        {
            "month_form": month_form,
            "month": month,
            "period": date(period.year, period.month, 1),
            "previous_month": adjacent(-1),
            "next_month": adjacent(1),
            "rows": rows,
            "form": form,
            "error": error,
            "selected_tab": "app_budgets",
        },
        status=status,
    )
