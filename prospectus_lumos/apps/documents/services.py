from __future__ import annotations

from decimal import Decimal
from typing import List

from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Count, Q, Sum

from prospectus_lumos.apps.accounts.models import DocumentSource
from prospectus_lumos.apps.documents.models import Document
from prospectus_lumos.apps.transactions.models import Transaction

MANUAL_SOURCE_NAME = "Manual entries"
SUMMARY_FIELDS = ["total_income", "total_expenses", "income_count", "expenses_count"]
_WRITTEN_SUMMARY_FIELDS = [*SUMMARY_FIELDS, "updated_at"]


def lock_documents(*documents: Document) -> List[Document]:
    """Lock the given monthly documents for update and return them in a stable order.

    Must be called inside an atomic block. Rows are locked by ascending primary key so two
    callers that touch the same pair of months — a transaction moving from one month to another,
    for instance — cannot deadlock against each other. Duplicates are collapsed, so passing the
    same document twice locks it once.

    :param documents: saved documents to lock.
    :return: the locked documents, ordered by primary key.
    :raises Document.DoesNotExist: if a document no longer exists.
    :raises ValueError: if a document has not been saved yet.
    :raises TransactionManagementError: if there is no enclosing atomic block to hold the locks.
    """

    if not transaction.get_connection().in_atomic_block:
        raise transaction.TransactionManagementError("lock_documents() requires an enclosing atomic block")

    if any(document.pk is None for document in documents):
        raise ValueError("documents must be saved before they can be locked")

    document_ids = sorted({document.pk for document in documents})
    if not document_ids:
        return []

    locked = {
        document.pk: document
        for document in Document.objects.select_for_update().filter(pk__in=document_ids).order_by("pk")
    }
    missing = [document_id for document_id in document_ids if document_id not in locked]
    if missing:
        raise Document.DoesNotExist(f"Monthly documents no longer exist: {missing}")

    return [locked[document_id] for document_id in document_ids]


def resolve_monthly_document(
    *,
    user: User,
    year: int,
    month: int,
    source: DocumentSource | None = None,
    for_update: bool = False,
) -> Document:
    """Return the user's single document for a month, creating it when it does not exist yet.

    There is exactly one document per user and month, whatever created it, so manual entry and
    imports both land on the same row. An existing document is returned untouched, including its
    source and import metadata.

    :param user: owner of the document.
    :param year: calendar year of the month.
    :param month: calendar month, 1-12.
    :param source: source to record when a document has to be created; when omitted the user's
        manual source is used or created.
    :param for_update: lock the resolved row for update; requires an enclosing atomic block.
    :return: the user's document for that month.
    :raises ValueError: if the month is out of range, or the reserved manual source name is taken
        by a source of another type.
    :raises TransactionManagementError: if ``for_update`` is set without an enclosing atomic block,
        which would release the lock as soon as this call returns.
    """

    if not 1 <= month <= 12:
        raise ValueError("month must be between 1 and 12")

    if for_update and not transaction.get_connection().in_atomic_block:
        raise transaction.TransactionManagementError(
            "resolve_monthly_document(for_update=True) requires an enclosing atomic block"
        )

    with transaction.atomic():
        existing_document = Document.objects.filter(user=user, year=year, month=month).first()
        if existing_document is not None:
            return lock_documents(existing_document)[0] if for_update else existing_document

        document, _ = Document.objects.get_or_create(
            user=user,
            year=year,
            month=month,
            defaults={"source": source if source is not None else _resolve_manual_source(user=user)},
        )
        return lock_documents(document)[0] if for_update else document


def recalculate_document_summary(*, document: Document) -> Document:
    """Recalculate a document's income and expense totals and counts from its current rows.

    The document row is locked for the rest of the enclosing transaction before it is read, so
    concurrent writers to the same month produce totals that match the rows that survive.

    :param document: saved document to summarize.
    :return: the same instance, with refreshed summary fields saved.
    :raises Document.DoesNotExist: if the document no longer exists.
    :raises ValueError: if the document has not been saved yet.
    """

    with transaction.atomic():
        locked_document = lock_documents(document)[0]

        income = Q(transaction_type=Transaction.TransactionType.INCOME)
        expense = Q(transaction_type=Transaction.TransactionType.EXPENSE)
        summary = Transaction.objects.filter(document=locked_document).aggregate(
            total_income=Sum("amount", filter=income),
            total_expenses=Sum("amount", filter=expense),
            income_count=Count("pk", filter=income),
            expenses_count=Count("pk", filter=expense),
        )

        locked_document.total_income = summary["total_income"] or Decimal("0")
        locked_document.total_expenses = summary["total_expenses"] or Decimal("0")
        locked_document.income_count = summary["income_count"]
        locked_document.expenses_count = summary["expenses_count"]
        locked_document.save(update_fields=_WRITTEN_SUMMARY_FIELDS)

    for field in _WRITTEN_SUMMARY_FIELDS:
        setattr(document, field, getattr(locked_document, field))
    return document


def _resolve_manual_source(*, user: User) -> DocumentSource:
    """Return the user's manual document source, creating the reserved one when there is none.

    :param user: owner of the source.
    :return: a manual document source owned by the user.
    :raises ValueError: if the reserved manual source name belongs to a source of another type.
    """

    existing_manual_source = (
        DocumentSource.objects.filter(user=user, source_type=DocumentSource.SourceType.MANUAL).order_by("pk").first()
    )
    if existing_manual_source is not None:
        return existing_manual_source

    manual_source, _ = DocumentSource.objects.get_or_create(
        user=user,
        name=MANUAL_SOURCE_NAME,
        defaults={"source_type": DocumentSource.SourceType.MANUAL},
    )
    if manual_source.source_type != DocumentSource.SourceType.MANUAL:
        raise ValueError(f'The reserved document source name "{MANUAL_SOURCE_NAME}" is already in use')
    return manual_source
