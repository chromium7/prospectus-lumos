from __future__ import annotations

from django.contrib.auth.models import User

from prospectus_lumos.apps.accounts.models import DocumentSource
from prospectus_lumos.apps.documents.models import Document

MANUAL_SOURCE_NAME = "Manual entries"


def resolve_monthly_document(*, user: User, year: int, month: int) -> Document:
    """Return the user's existing monthly document or create a manual-only one."""

    if not 1 <= month <= 12:
        raise ValueError("month must be between 1 and 12")

    existing_document = Document.objects.filter(user=user, year=year, month=month).first()
    if existing_document is not None:
        return existing_document

    existing_manual_source = (
        DocumentSource.objects.filter(user=user, source_type=DocumentSource.SourceType.MANUAL).order_by("pk").first()
    )
    if existing_manual_source is not None:
        manual_source = existing_manual_source
    else:
        manual_source, _ = DocumentSource.objects.get_or_create(
            user=user,
            name=MANUAL_SOURCE_NAME,
            defaults={"source_type": DocumentSource.SourceType.MANUAL},
        )
    if manual_source.source_type != DocumentSource.SourceType.MANUAL:
        raise ValueError(f'The reserved document source name "{MANUAL_SOURCE_NAME}" is already in use')

    document, _ = Document.objects.get_or_create(
        user=user,
        year=year,
        month=month,
        defaults={"source": manual_source},
    )
    return document
