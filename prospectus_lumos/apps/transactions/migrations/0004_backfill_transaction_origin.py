from typing import Any

from django.db import migrations


SOURCE_ORIGINS = {
    "manual": "manual",
    "google_drive": "google_sheets",
    "direct_upload": "direct_upload",
}


def backfill_transaction_origins(apps: Any, schema_editor: Any) -> None:
    """Classify historical rows, rejecting any lineage that cannot be proven."""

    transaction_model = apps.get_model("transactions", "Transaction")
    ambiguous_count = 0
    ambiguous_preview = []

    transactions = (
        transaction_model.objects.filter(origin__isnull=True)
        .select_related("document__source")
        .order_by("pk")
    )
    for transaction in transactions.iterator(chunk_size=1_000):
        if transaction.document_id is None:
            if transaction.user_id is None:
                ambiguous_count += 1
                if len(ambiguous_preview) < 20:
                    ambiguous_preview.append((transaction.pk, "missing owner and document"))
            continue

        source_type = transaction.document.source.source_type
        if source_type not in SOURCE_ORIGINS:
            ambiguous_count += 1
            if len(ambiguous_preview) < 20:
                ambiguous_preview.append((transaction.pk, f"document source type {source_type!r}"))

    if ambiguous_count:
        preview = ", ".join(f"id={transaction_id} ({reason})" for transaction_id, reason in ambiguous_preview)
        remainder = ambiguous_count - len(ambiguous_preview)
        if remainder:
            preview = f"{preview}, and {remainder} more"
        raise RuntimeError(
            f"Cannot classify origin for {ambiguous_count} transaction(s): {preview}. "
            "Correct their document source or ownership, then rerun the migration."
        )

    transaction_model.objects.filter(
        origin__isnull=True,
        document__isnull=True,
        user__isnull=False,
    ).update(origin="manual")
    for source_type, origin in SOURCE_ORIGINS.items():
        transaction_model.objects.filter(
            origin__isnull=True,
            document__source__source_type=source_type,
        ).update(origin=origin)


def clear_transaction_origins(apps: Any, schema_editor: Any) -> None:
    """Clear values before the preceding nullable schema state is restored."""

    transaction_model = apps.get_model("transactions", "Transaction")
    transaction_model.objects.update(origin=None)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0002_alter_documentsource_source_type"),
        ("documents", "0002_alter_document_csv_file"),
        ("transactions", "0003_transaction_origin_nullable"),
    ]

    operations = [
        migrations.RunPython(backfill_transaction_origins, clear_transaction_origins),
    ]
