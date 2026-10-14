from __future__ import annotations

from collections.abc import Collection
from datetime import date as date_type
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.core.validators import DecimalValidator, ProhibitNullCharactersValidator


_NULL_CHARACTER_VALIDATOR = ProhibitNullCharactersValidator()


def validate_choice(*, value: str, choices: Collection[str], message: str) -> str:
    """Return a value when it belongs to the supplied choices.

    :param value: submitted choice value.
    :param choices: accepted values.
    :param message: user-safe invalid-choice message.
    :return: the validated choice.
    :raises ValidationError: if the value is not an accepted choice.
    """

    if value not in choices:
        raise ValidationError(message, code="invalid_choice")
    return value


def validate_date_only(value: date_type) -> date_type:
    """Return a calendar date while rejecting strings and datetimes.

    :param value: submitted date-only value.
    :return: the validated calendar date.
    :raises ValidationError: if the value is not exactly a date.
    """

    if not isinstance(value, date_type) or type(value) is not date_type:
        raise ValidationError("Enter a valid local date.", code="invalid")
    return value


def validate_positive_decimal(
    *,
    value: Decimal,
    max_digits: int,
    decimal_places: int,
    label: str,
) -> Decimal:
    """Return a positive, finite Decimal within the supplied precision.

    :param value: submitted Decimal value.
    :param max_digits: maximum total digits allowed.
    :param decimal_places: maximum fractional digits allowed.
    :param label: user-facing value label for validation messages.
    :return: the validated Decimal.
    :raises ValidationError: if the value is invalid, non-positive, or outside the precision.
    """

    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValidationError(f"Enter a valid {label}.", code="invalid")
    if value <= 0:
        raise ValidationError("Enter an amount greater than zero.", code="non_positive")
    try:
        DecimalValidator(max_digits=max_digits, decimal_places=decimal_places)(value)
    except InvalidOperation:
        raise ValidationError(f"Enter a valid {label}.", code="invalid")
    return value


def validate_required_text(*, value: str, max_length: int) -> str:
    """Strip and validate a required bounded text value.

    :param value: submitted text value.
    :param max_length: maximum length after surrounding whitespace is stripped.
    :return: the stripped text.
    :raises ValidationError: if the value is not text, blank, too long, or contains a null character.
    """

    if not isinstance(value, str):
        raise ValidationError("Enter a valid text value.", code="invalid")
    cleaned = value.strip()
    if not cleaned:
        raise ValidationError("This field is required.", code="required")
    if len(cleaned) > max_length:
        raise ValidationError(
            f"Ensure this value has at most {max_length} characters.",
            code="max_length",
            params={"limit_value": max_length, "show_value": len(cleaned), "value": cleaned},
        )
    _NULL_CHARACTER_VALIDATOR(cleaned)
    return cleaned
