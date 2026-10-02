from datetime import date
from decimal import Decimal
from typing import Any

from django import forms
from django.contrib.auth.models import User

from prospectus_lumos.apps.transactions.models import Transaction


class TransactionForm(forms.ModelForm):
    """Validate a manual income or expense owned directly by a user."""

    transaction_type = forms.ChoiceField(
        choices=Transaction.TransactionType.choices,
        widget=forms.RadioSelect(),
    )
    date = forms.DateField(widget=forms.DateInput(attrs={"class": "form-control", "type": "date"}))

    class Meta:
        model = Transaction
        fields = ("transaction_type", "amount", "category", "date", "description")
        widgets = {
            "transaction_type": forms.RadioSelect(),
            "amount": forms.NumberInput(
                attrs={
                    "class": "form-control transaction-amount",
                    "min": "0.01",
                    "step": "0.01",
                    "inputmode": "decimal",
                    "autofocus": True,
                }
            ),
            "category": forms.TextInput(
                attrs={"class": "form-control", "list": "recent-transaction-categories", "autocomplete": "off"}
            ),
            "description": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "What was this for?", "autocomplete": "off"}
            ),
        }
        labels = {"transaction_type": "Type"}

    def __init__(self, *args: Any, user: User, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.user = user
        self.fields["category"].required = True
        self.fields["category"].error_messages["required"] = "Choose or enter a category."

    def clean_amount(self) -> Decimal:
        amount: Decimal = self.cleaned_data["amount"]
        if amount <= 0:
            raise forms.ValidationError("Enter an amount greater than zero.", code="non_positive")
        return amount

    def clean_category(self) -> str:
        category = str(self.cleaned_data["category"]).strip()
        if not category:
            raise forms.ValidationError("Choose or enter a category.", code="required")
        return category

    def save(self, commit: bool = True) -> Transaction:
        transaction = super().save(commit=False)
        transaction.user = self.user
        transaction.document = None
        occurred_on: date = self.cleaned_data["date"]
        transaction.date = occurred_on.isoformat()
        if commit:
            transaction.save()
        return transaction
