from __future__ import annotations

from datetime import date as date_type
from decimal import Decimal

from django.contrib.auth.models import User
from django.db import transaction

from prospectus_lumos.apps.documents.services import recalculate_document_summary, resolve_monthly_document
from prospectus_lumos.apps.transactions.models import Transaction


def create_manual_transaction(
    *,
    user: User,
    transaction_type: str,
    date: date_type,
    amount: Decimal,
    description: str,
    category: str,
) -> Transaction:
    """Create a pre-validated manual income or expense in the owner's monthly document.

    The owner comes from trusted caller context rather than submitted data. The transaction and
    its document summary are committed together, and the summary includes both imported and
    manual rows already present in the month.

    :param user: authenticated owner of the transaction.
    :param transaction_type: validated ``income`` or ``expense`` value.
    :param date: validated calendar date in the application's local Asia/Jakarta date domain.
    :param amount: validated positive IDR value with at most two decimal places.
    :param description: validated and normalized transaction description.
    :param category: validated and normalized text category.
    :return: the newly created manual transaction.
    """

    with transaction.atomic():
        document = resolve_monthly_document(
            user=user,
            year=date.year,
            month=date.month,
            for_update=True,
        )
        created = Transaction.objects.create(
            user=user,
            document=document,
            transaction_type=transaction_type,
            date=date.isoformat(),
            amount=amount,
            description=description,
            category=category,
            origin=Transaction.Origin.MANUAL,
        )
        recalculate_document_summary(document=document)
    return created
