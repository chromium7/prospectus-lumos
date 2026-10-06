from __future__ import annotations

from datetime import date as date_type
from decimal import Decimal, InvalidOperation

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.validators import DecimalValidator, ProhibitNullCharactersValidator
from django.db import transaction

from prospectus_lumos.apps.documents.services import recalculate_document_summary, resolve_monthly_document
from prospectus_lumos.apps.transactions.models import Transaction


_AMOUNT_VALIDATOR = DecimalValidator(max_digits=15, decimal_places=2)
_NULL_CHARACTER_VALIDATOR = ProhibitNullCharactersValidator()


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

    validated_type = _validate_transaction_type(transaction_type)
    validated_date = _validate_date(date)
    validated_amount = _validate_amount(amount)
    validated_description = _validate_text(
        value=description,
        field="description",
        max_length=500,
    )
    validated_category = _validate_text(
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


def _validate_transaction_type(value: str) -> str:
    """Return a supported manual transaction type."""

    if value not in Transaction.TransactionType.values:
        raise ValidationError({"transaction_type": ValidationError("Choose income or expense.", code="invalid_choice")})
    return value


def _validate_date(value: date_type) -> date_type:
    """Return a date-only value suitable for local monthly resolution."""

    if not isinstance(value, date_type) or type(value) is not date_type:
        raise ValidationError({"date": ValidationError("Enter a valid local date.", code="invalid")})
    return value


def _validate_amount(value: Decimal) -> Decimal:
    """Return a positive, finite IDR Decimal that fits the existing model field."""

    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValidationError({"amount": ValidationError("Enter a valid IDR amount.", code="invalid")})
    if value <= 0:
        raise ValidationError({"amount": ValidationError("Enter an amount greater than zero.", code="non_positive")})
    try:
        _AMOUNT_VALIDATOR(value)
    except InvalidOperation:
        raise ValidationError({"amount": ValidationError("Enter a valid IDR amount.", code="invalid")})
    except ValidationError as error:
        raise ValidationError({"amount": error.error_list})
    return value


def _validate_text(*, value: str, field: str, max_length: int) -> str:
    """Strip and validate a required transaction text field."""

    if not isinstance(value, str):
        raise ValidationError({field: ValidationError("Enter a valid text value.", code="invalid")})
    cleaned = value.strip()
    if not cleaned:
        raise ValidationError({field: ValidationError("This field is required.", code="required")})
    if len(cleaned) > max_length:
        raise ValidationError(
            {
                field: ValidationError(
                    f"Ensure this value has at most {max_length} characters.",
                    code="max_length",
                    params={"limit_value": max_length, "show_value": len(cleaned), "value": cleaned},
                )
            }
        )
    try:
        _NULL_CHARACTER_VALIDATOR(cleaned)
    except ValidationError as error:
        raise ValidationError({field: error.error_list})
    return cleaned
