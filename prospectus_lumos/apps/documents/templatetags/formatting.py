from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from django import template

register = template.Library()


def _to_decimal(value: Any) -> Decimal:
    try:
        if isinstance(value, Decimal):
            return value
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


@register.filter(name="intdot")
def intdot(value: Any) -> str:
    """Format number with dot as thousands separator (e.g., 1.234.567).

    Keeps no decimal places; rounds like Python formatting.
    """
    number = _to_decimal(value)
    sign = "-" if number < 0 else ""
    number = abs(number)
    grouped = f"{number:,.0f}".replace(",", ".")
    return f"{sign}{grouped}"


@register.filter(name="idr")
def idr(value: Any) -> str:
    """Format number as Indonesian Rupiah string: Rp1.234.567.

    Example: {{ 1234567|idr }} -> "Rp1.234.567"
    """
    return f"Rp{intdot(value)}"
