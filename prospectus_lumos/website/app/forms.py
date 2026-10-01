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
            option["attrs"].update(
                {
                    "data-choice-group": "transaction-category",
                    "data-choice-value": value.instance.type,
                }
            )
        return option


class TransactionForm(forms.ModelForm):
    """Validate one user-owned income or expense entry."""

    submission_token = forms.CharField(widget=forms.HiddenInput())

    class Meta:
        model = LedgerTransaction
        fields = (
            "type",
            "amount",
            "account",
            "category",
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
            "occurred_on": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "payee": forms.TextInput(attrs={"class": "form-control", "placeholder": "Who was this with?"}),
            "note": forms.Textarea(attrs={"class": "form-control", "placeholder": "Add a note", "rows": 3}),
        }
        labels = {
            "account": "Account",
            "occurred_on": "Date",
        }

    def __init__(self, *args: Any, user: User, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.user = user
        type_field = cast(forms.ChoiceField, self.fields["type"])
        type_field.choices = (
            (LedgerTransaction.Type.EXPENSE, "Expense"),
            (LedgerTransaction.Type.INCOME, "Income"),
        )
        type_field.initial = self.initial.get("type", LedgerTransaction.Type.EXPENSE)
        self.fields["occurred_on"].initial = self.initial.get("occurred_on", timezone.localdate())
        account_field = cast(forms.ModelChoiceField, self.fields["account"])
        category_field = cast(forms.ModelChoiceField, self.fields["category"])
        account_field.queryset = FinancialAccount.objects.for_user(user).active()
        category_field.queryset = Category.objects.for_user(user).active()
        category_field.required = True
        category_field.empty_label = "Choose a category"
        category_field.error_messages["required"] = "Choose a category."

    def clean(self) -> dict[str, Any]:
        cleaned_data = super().clean()
        transaction_type = cleaned_data.get("type")
        category = cleaned_data.get("category")
        if self.errors:
            return cleaned_data
        if category.type != transaction_type:
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
