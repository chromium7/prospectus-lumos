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
    origin_ids = {origin: [] for origin in SOURCE_ORIGINS.values()}
    ambiguous = []

    transactions = transaction_model.objects.filter(origin__isnull=True).select_related("document__source")
    for transaction in transactions.iterator(chunk_size=1_000):
        if transaction.document_id is None:
            if transaction.user_id is None:
                ambiguous.append((transaction.pk, "missing owner and document"))
                continue
            origin_ids["manual"].append(transaction.pk)
            continue

        source_type = transaction.document.source.source_type
        origin = SOURCE_ORIGINS.get(source_type)
        if origin is None:
            ambiguous.append((transaction.pk, f"document source type {source_type!r}"))
            continue
        origin_ids[origin].append(transaction.pk)

    if ambiguous:
        preview = ", ".join(f"id={transaction_id} ({reason})" for transaction_id, reason in ambiguous[:20])
        remainder = len(ambiguous) - 20
        if remainder:
            preview = f"{preview}, and {remainder} more"
        raise RuntimeError(
            f"Cannot classify origin for {len(ambiguous)} transaction(s): {preview}. "
            "Correct their document source or ownership, then rerun the migration."
        )

    for origin, transaction_ids in origin_ids.items():
        transaction_model.objects.filter(pk__in=transaction_ids).update(origin=origin)


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
