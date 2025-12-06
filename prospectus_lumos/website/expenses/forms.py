from typing import Any

from django import forms
from django.db.models import QuerySet, TextChoices

from prospectus_lumos.apps.transactions.models import Transaction


class DocumentTransactionFilterForm(forms.Form):
    """Filter and sorting options for a single document's transactions."""

    class Sort(TextChoices):
        DATE = "date", "Date"
        AMOUNT = "amount", "Amount"

    class Direction(TextChoices):
        ASC = "asc", "Ascending"
        DESC = "desc", "Descending"

    class TxType(TextChoices):
        ALL = "", "All types"
        EXPENSE = Transaction.TransactionType.EXPENSE, "Expenses"
        INCOME = Transaction.TransactionType.INCOME, "Income"

    # Convenience aliases used elsewhere (e.g., views, templates)
    SORT_DATE = Sort.DATE
    SORT_AMOUNT = Sort.AMOUNT
    DIRECTION_ASC = Direction.ASC
    DIRECTION_DESC = Direction.DESC

    # Sort controls are kept as hidden inputs; the UI for changing sort is the table header
    sort = forms.TypedChoiceField(
        choices=Sort.choices,
        required=False,
        widget=forms.HiddenInput,
        coerce=str,
    )
    direction = forms.TypedChoiceField(
        choices=Direction.choices,
        required=False,
        widget=forms.HiddenInput,
        coerce=str,
    )
    transaction_type = forms.TypedChoiceField(
        choices=TxType.choices,
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
        label="Transaction type",
        coerce=str,
    )

    def clean(self) -> dict[str, Any]:
        data = super().clean()

        sort = data.get("sort") or self.Sort.DATE  # type: ignore[assignment]
        if sort not in {self.Sort.DATE, self.Sort.AMOUNT}:
            sort = self.Sort.DATE

        direction = data.get("direction") or self.Direction.ASC  # type: ignore[assignment]
        if direction not in {self.Direction.ASC, self.Direction.DESC}:
            direction = self.Direction.ASC

        tx_type = data.get("transaction_type") or self.TxType.ALL
        valid_types = {self.TxType.ALL, self.TxType.EXPENSE, self.TxType.INCOME}
        if tx_type not in valid_types:
            tx_type = self.TxType.ALL

        data["sort"] = str(sort)
        data["direction"] = str(direction)
        data["transaction_type"] = str(tx_type)
        return data

    def apply(self, qs: QuerySet[Transaction]) -> QuerySet[Transaction]:
        """Apply type filter and ordering to the given queryset."""
        if not self.is_valid():
            # fall back to default ordering by date
            return qs.order_by("date", "id")

        sort: str = self.cleaned_data["sort"]
        direction: str = self.cleaned_data["direction"]
        tx_type: str = self.cleaned_data["transaction_type"]

        if tx_type:
            qs = qs.filter(transaction_type=tx_type)

        prefix = "" if direction == self.Direction.ASC else "-"

        if sort == self.Sort.AMOUNT:
            qs = qs.order_by(f"{prefix}amount", "id")
        else:
            qs = qs.order_by(f"{prefix}date", "id")

        return qs
