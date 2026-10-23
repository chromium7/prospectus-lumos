from __future__ import annotations

from datetime import date as date_type
from decimal import Decimal

from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Q

from prospectus_lumos.apps.documents.services import (
    lock_documents,
    recalculate_document_summary,
    resolve_monthly_document,
)
from prospectus_lumos.apps.transactions.models import Transaction


class ImportedTransactionEditError(ValueError):
    """Raised when a manual edit is attempted for a source-managed transaction."""


class TransactionEditConflict(ValueError):
    """Raised when a transaction moves while an edit is acquiring its month locks."""


class ImportedTransactionDeleteError(ValueError):
    """Raised when deletion is attempted for a source-managed transaction."""


class TransactionDeleteConflict(ValueError):
    """Raised when a transaction moves while deletion acquires its month lock."""


@transaction.atomic
def delete_manual_transaction(*, user: User, transaction_id: int) -> None:
    """Delete an owner's manual row and atomically refresh its combined monthly summary.

    Callers must obtain confirmation before invoking this destructive operation. The monthly
    document and all retained transaction IDs and import metadata are preserved. Owner-only
    rows without a document can also be deleted; they have no monthly summary to refresh.

    :param user: authenticated owner from trusted caller context.
    :param transaction_id: primary key of the confirmed transaction to delete.
    :return: None.
    :raises Transaction.DoesNotExist: if the row does not exist or belongs to another user.
    :raises ImportedTransactionDeleteError: if the row is managed by an import source.
    :raises TransactionDeleteConflict: if the row moved while its month lock was acquired.
    """

    ownership = Q(user=user) | Q(document__user=user)
    existing = Transaction.objects.select_related("document").filter(ownership).get(pk=transaction_id)
    if existing.origin != Transaction.Origin.MANUAL:
        raise ImportedTransactionDeleteError("Imported transactions must be deleted through their source workflow.")

    document = lock_documents(existing.document)[0] if existing.document is not None else None
    deleted = Transaction.objects.select_for_update(of=("self",)).filter(ownership).get(pk=transaction_id)
    if deleted.origin != Transaction.Origin.MANUAL:
        raise ImportedTransactionDeleteError("Imported transactions must be deleted through their source workflow.")
    if deleted.document_id != existing.document_id:
        raise TransactionDeleteConflict("The transaction moved while it was being deleted; reload and try again.")

    deleted.delete()
    if document is not None:
        recalculate_document_summary(document=document)


@transaction.atomic
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


@transaction.atomic
def edit_manual_transaction(
    *,
    user: User,
    transaction_id: int,
    transaction_type: str,
    date: date_type,
    amount: Decimal,
    description: str,
    category: str,
) -> Transaction:
    """Edit a pre-validated manual transaction owned by the user.

    The transaction keeps its identity and origin. A date change moves it to the resolved monthly
    document, while all affected document rows are locked in stable primary-key order and their
    combined imported/manual summaries are updated in the same database transaction.

    :param user: authenticated owner of the transaction.
    :param transaction_id: primary key of the transaction to edit.
    :param transaction_type: validated ``income`` or ``expense`` value.
    :param date: validated calendar date in the application's local Asia/Jakarta date domain.
    :param amount: validated positive IDR value with at most two decimal places.
    :param description: validated and normalized transaction description.
    :param category: validated and normalized text category.
    :return: the updated manual transaction.
    :raises Transaction.DoesNotExist: if the transaction is not owned by the user.
    :raises ImportedTransactionEditError: if the row is managed by an import source.
    :raises TransactionEditConflict: if the row moved while its month locks were acquired.
    """

    ownership = Q(user=user) | Q(document__user=user)
    existing = Transaction.objects.select_related("document").filter(ownership).get(pk=transaction_id)
    if existing.origin != Transaction.Origin.MANUAL:
        raise ImportedTransactionEditError("Imported transactions must be edited through their source workflow.")
    if existing.document is None:
        raise TransactionEditConflict("The manual transaction is not assigned to a monthly document.")

    destination = resolve_monthly_document(user=user, year=date.year, month=date.month)
    locked_documents = lock_documents(existing.document, destination)
    documents_by_id = {document.pk: document for document in locked_documents}

    edited = Transaction.objects.select_for_update(of=("self",)).filter(ownership).get(pk=transaction_id)
    if edited.origin != Transaction.Origin.MANUAL:
        raise ImportedTransactionEditError("Imported transactions must be edited through their source workflow.")
    if edited.document_id != existing.document_id:
        raise TransactionEditConflict("The transaction moved while it was being edited; reload and try again.")

    edited.document = documents_by_id[destination.pk]
    edited.transaction_type = transaction_type
    edited.date = date.isoformat()
    edited.amount = amount
    edited.description = description
    edited.category = category
    edited.save(
        update_fields=[
            "document",
            "transaction_type",
            "date",
            "amount",
            "description",
            "category",
        ]
    )

    for document in locked_documents:
        recalculate_document_summary(document=document)

    return edited
