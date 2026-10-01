from __future__ import annotations

from typing import Any, cast

from django import forms
from django.contrib.auth.models import User
from django.utils import timezone

from prospectus_lumos.apps.ledger.models import Category, FinancialAccount, LedgerTransaction


class CategorySelect(forms.Select):
    """Expose category type to the optional browser enhancement."""

    def create_option(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        option = super().create_option(*args, **kwargs)
        value = option.get("value")
        if value and getattr(value, "instance", None):
            option["attrs"]["data-category-type"] = value.instance.type
        return option


class TransactionForm(forms.ModelForm):
    """Validate one user-owned income, expense, or transfer entry."""

    submission_token = forms.CharField(widget=forms.HiddenInput())

    class Meta:
        model = LedgerTransaction
        fields = (
            "type",
            "amount",
            "account",
            "category",
            "transfer_account",
            "occurred_on",
            "payee",
            "note",
        )
        widgets = {
            "type": forms.RadioSelect(),
            "amount": forms.NumberInput(
                attrs={
                    "autofocus": True,
                    "class": "form-control transaction-amount",
                    "inputmode": "decimal",
                    "min": "0.01",
                    "placeholder": "0",
                    "step": "0.01",
                }
            ),
            "account": forms.Select(attrs={"class": "form-select"}),
            "category": CategorySelect(attrs={"class": "form-select"}),
            "transfer_account": forms.Select(attrs={"class": "form-select"}),
            "occurred_on": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "payee": forms.TextInput(attrs={"class": "form-control", "placeholder": "Who was this with?"}),
            "note": forms.Textarea(attrs={"class": "form-control", "placeholder": "Add a note", "rows": 3}),
        }
        labels = {
            "account": "Account",
            "transfer_account": "To account",
            "occurred_on": "Date",
        }

    def __init__(self, *args: Any, user: User, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.user = user
        type_field = cast(forms.ChoiceField, self.fields["type"])
        type_field.choices = (
            (LedgerTransaction.Type.EXPENSE, "Expense"),
            (LedgerTransaction.Type.INCOME, "Income"),
            (LedgerTransaction.Type.TRANSFER, "Transfer"),
        )
        type_field.initial = self.initial.get("type", LedgerTransaction.Type.EXPENSE)
        self.fields["occurred_on"].initial = self.initial.get("occurred_on", timezone.localdate())
        account_field = cast(forms.ModelChoiceField, self.fields["account"])
        category_field = cast(forms.ModelChoiceField, self.fields["category"])
        transfer_account_field = cast(forms.ModelChoiceField, self.fields["transfer_account"])
        account_field.queryset = FinancialAccount.objects.for_user(user).active()
        category_field.queryset = Category.objects.for_user(user).active()
        transfer_account_field.queryset = FinancialAccount.objects.for_user(user).active()
        category_field.required = False
        transfer_account_field.required = False
        category_field.empty_label = "Choose a category"
        transfer_account_field.empty_label = "Choose a destination"

    def clean(self) -> dict[str, Any]:
        cleaned_data = super().clean()
        transaction_type = cleaned_data.get("type")
        category = cleaned_data.get("category")
        account = cleaned_data.get("account")
        transfer_account = cleaned_data.get("transfer_account")

        if transaction_type == LedgerTransaction.Type.TRANSFER:
            cleaned_data["category"] = None
            if transfer_account is None:
                self.add_error(
                    "transfer_account",
                    forms.ValidationError("Choose the account receiving this transfer.", code="required"),
                )
            elif account == transfer_account:
                self.add_error(
                    "transfer_account",
                    forms.ValidationError("Choose a different destination account.", code="same_account"),
                )
        elif transaction_type in (LedgerTransaction.Type.EXPENSE, LedgerTransaction.Type.INCOME):
            cleaned_data["transfer_account"] = None
            if category is None:
                self.add_error("category", forms.ValidationError("Choose a category.", code="required"))
            elif category.type != transaction_type:
                label = "expense" if transaction_type == LedgerTransaction.Type.EXPENSE else "income"
                self.add_error(
                    "category",
                    forms.ValidationError(f"Choose an {label} category.", code="category_type"),
                )
        return cleaned_data

    def save(self, commit: bool = True) -> LedgerTransaction:
        """Attach the authenticated owner before persisting the transaction."""

        transaction = super().save(commit=False)
        transaction.user = self.user
        if commit:
            transaction.save()
        return transaction
