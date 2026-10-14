from __future__ import annotations

from datetime import date as date_type
from decimal import Decimal

from django.contrib.auth.models import User
from django.db import transaction

from prospectus_lumos.apps.documents.services import recalculate_document_summary, resolve_monthly_document
from prospectus_lumos.apps.transactions.models import Transaction
from prospectus_lumos.core.validators import (
    validate_choice,
    validate_date_only,
    validate_positive_decimal,
    validate_required_text,
)


def create_manual_transaction(
    *,
    user: User,
    transaction_type: str,
    date: date_type,
    amount: Decimal,
    description: str,
    category: str,
) -> Transaction:
    """Validate and create one manual income or expense in the owner's monthly document.

    The owner comes from trusted caller context rather than submitted data. The transaction and
    its document summary are committed together, and the summary includes both imported and
    manual rows already present in the month.

    :param user: authenticated owner of the transaction.
    :param transaction_type: ``income`` or ``expense``.
    :param date: calendar date in the application's local Asia/Jakarta date domain.
    :param amount: positive IDR value with at most two decimal places.
    :param description: required transaction description, stripped of surrounding whitespace.
    :param category: required text category, stripped of surrounding whitespace.
    :return: the newly created manual transaction.
    :raises ValidationError: if any transaction value is invalid.
    """

    validated_type = validate_choice(
        value=transaction_type,
        field="transaction_type",
        choices=Transaction.TransactionType.values,
        message="Choose income or expense.",
    )
    validated_date = validate_date_only(value=date, field="date")
    validated_amount = validate_positive_decimal(
        value=amount,
        field="amount",
        max_digits=15,
        decimal_places=2,
        label="IDR amount",
    )
    validated_description = validate_required_text(
        value=description,
        field="description",
        max_length=500,
    )
    validated_category = validate_required_text(
        value=category,
        field="category",
        max_length=100,
    )

    with transaction.atomic():
        document = resolve_monthly_document(
            user=user,
            year=validated_date.year,
            month=validated_date.month,
            for_update=True,
        )
        created = Transaction.objects.create(
            user=user,
            document=document,
            transaction_type=validated_type,
            date=validated_date.isoformat(),
            amount=validated_amount,
            description=validated_description,
            category=validated_category,
            origin=Transaction.Origin.MANUAL,
        )
        recalculate_document_summary(document=document)
    return created
