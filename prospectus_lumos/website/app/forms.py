from datetime import date as date_type
from decimal import Decimal
from typing import Any

from django import forms
from django.contrib.auth.models import User

from prospectus_lumos.apps.transactions.models import Transaction
from prospectus_lumos.apps.transactions.services import create_manual_transaction, edit_manual_transaction
from prospectus_lumos.core.validators import (
    validate_choice,
    validate_date_only,
    validate_positive_decimal,
    validate_required_text,
)


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
        self.transaction_id = self.instance.pk
        self.fields["category"].required = True
        self.fields["category"].error_messages["required"] = "Choose or enter a category."

    def clean_amount(self) -> Decimal:
        amount: Decimal = self.cleaned_data["amount"]
        return validate_positive_decimal(
            value=amount,
            max_digits=15,
            decimal_places=2,
            label="IDR amount",
        )

    def clean_transaction_type(self) -> str:
        transaction_type: str = self.cleaned_data["transaction_type"]
        return validate_choice(
            value=transaction_type,
            choices=Transaction.TransactionType.values,
            message="Choose income or expense.",
        )

    def clean_date(self) -> date_type:
        occurred_on: date_type = self.cleaned_data["date"]
        return validate_date_only(occurred_on)

    def clean_category(self) -> str:
        category: str = self.cleaned_data["category"]
        return validate_required_text(value=category, max_length=100)

    def clean_description(self) -> str:
        description: str = self.cleaned_data["description"]
        return validate_required_text(value=description, max_length=500)

    def save(self, commit: bool = True) -> Transaction:
        occurred_on: date_type = self.cleaned_data["date"]
        if commit:
            values = {
                "user": self.user,
                "transaction_type": self.cleaned_data["transaction_type"],
                "date": occurred_on,
                "amount": self.cleaned_data["amount"],
                "description": self.cleaned_data["description"],
                "category": self.cleaned_data["category"],
            }
            if self.transaction_id is None:
                self.instance = create_manual_transaction(**values)
            else:
                self.instance = edit_manual_transaction(
                    transaction_id=self.transaction_id,
                    **values,
                )
            return self.instance

        transaction = super().save(commit=False)
        transaction.user = self.user
        transaction.document = None
        transaction.date = occurred_on.isoformat()
        return transaction
