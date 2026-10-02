from django.contrib.auth.models import User
from django.db.models import Max

from prospectus_lumos.apps.transactions.models import Transaction


def recent_transaction_categories(user: User, *, limit: int = 5) -> list[str]:
    """Return distinct recently used category names owned by a user."""

    rows = (
        Transaction.for_user(user)
        .exclude(category="")
        .values("category")
        .annotate(last_used=Max("created_at"))
        .order_by("-last_used")
        .values_list("category", flat=True)[:limit]
    )
    return list(rows)
