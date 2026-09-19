"""Shared contract fixtures for the PWA budgeting work.

The dataset below is the single declarative description of the money cases every
layer has to agree on: two tenants, IDR amounts, month boundaries, transfers,
pending rows, deleted rows, and archived categories. Tests import the specs so
they do not each re-invent their own numbers, and ``build_legacy_dataset``
materialises the subset the legacy Google-Sheet models can represent so that the
baseline report and the later migration can be compared against the same data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from django.contrib.auth.models import User

from prospectus_lumos.apps.accounts.models import DocumentSource
from prospectus_lumos.apps.documents.models import Document
from prospectus_lumos.apps.transactions.models import Transaction


class EntryKind:
    """Canonical entry kinds. Transfers have no legacy representation."""

    INCOME = "income"
    EXPENSE = "expense"
    TRANSFER_OUT = "transfer_out"
    TRANSFER_IN = "transfer_in"


@dataclass(frozen=True)
class EntrySpec:
    """One money row, described independently of any storage model."""

    kind: str
    amount: Decimal
    date: str
    description: str
    category: str
    pending: bool = False
    deleted: bool = False
    archived_category: bool = False
    transfer_group: str = ""

    @property
    def is_legacy_visible(self) -> bool:
        """Whether a legacy monthly sheet would still carry this row."""
        return self.kind in (EntryKind.INCOME, EntryKind.EXPENSE) and not self.deleted


@dataclass(frozen=True)
class MonthSpec:
    """A tenant's month of entries."""

    username: str
    year: int
    month: int
    entries: tuple[EntrySpec, ...] = field(default_factory=tuple)

    @property
    def legacy_entries(self) -> tuple[EntrySpec, ...]:
        return tuple(entry for entry in self.entries if entry.is_legacy_visible)

    def legacy_total(self, transaction_type: str) -> Decimal:
        return sum(
            (entry.amount for entry in self.legacy_entries if entry.kind == transaction_type),
            Decimal("0"),
        )


TENANT_A = "contract_tenant_a"
TENANT_B = "contract_tenant_b"

CONTRACT_MONTHS: tuple[MonthSpec, ...] = (
    MonthSpec(
        username=TENANT_A,
        year=2025,
        month=1,
        entries=(
            EntrySpec(EntryKind.INCOME, Decimal("12500000.00"), "5/1/2025", "Salary January", "Salary"),
            EntrySpec(EntryKind.EXPENSE, Decimal("1750500.50"), "7/1/2025", "Groceries", "Food"),
            EntrySpec(EntryKind.EXPENSE, Decimal("99000.00"), "31/1/2025", "Month boundary coffee", "Food"),
            EntrySpec(
                EntryKind.EXPENSE,
                Decimal("250000.00"),
                "9/1/2025",
                "Pending card hold",
                "Shopping",
                pending=True,
            ),
            EntrySpec(
                EntryKind.EXPENSE,
                Decimal("400000.00"),
                "10/1/2025",
                "Removed duplicate",
                "Food",
                deleted=True,
            ),
            EntrySpec(
                EntryKind.TRANSFER_OUT,
                Decimal("3000000.00"),
                "15/1/2025",
                "To savings",
                "Transfer",
                transfer_group="a-jan-savings",
            ),
            EntrySpec(
                EntryKind.TRANSFER_IN,
                Decimal("3000000.00"),
                "15/1/2025",
                "From current account",
                "Transfer",
                transfer_group="a-jan-savings",
            ),
        ),
    ),
    MonthSpec(
        username=TENANT_A,
        year=2025,
        month=2,
        entries=(
            EntrySpec(EntryKind.INCOME, Decimal("12500000.00"), "5/2/2025", "Salary February", "Salary"),
            EntrySpec(
                EntryKind.EXPENSE,
                Decimal("875250.25"),
                "14/2/2025",
                "Legacy subscription",
                "Old Subscriptions",
                archived_category=True,
            ),
        ),
    ),
    MonthSpec(
        username=TENANT_B,
        year=2025,
        month=1,
        entries=(
            EntrySpec(EntryKind.INCOME, Decimal("8000000.00"), "3/1/2025", "Freelance invoice", "Freelance"),
            EntrySpec(EntryKind.EXPENSE, Decimal("2100000.00"), "12/1/2025", "Rent", "Housing"),
        ),
    ),
)


def build_legacy_dataset(password: str = "contract-pass") -> dict[str, User]:
    """Create the legacy rows for :data:`CONTRACT_MONTHS` and return the tenants by username.

    Only entries a legacy monthly sheet can carry are written: transfers and
    deleted rows are intentionally absent, which is what keeps transfers neutral
    in the legacy income/expense totals.
    """
    users: dict[str, User] = {}
    for month_spec in CONTRACT_MONTHS:
        user = users.get(month_spec.username)
        if user is None:
            user = User.objects.create_user(
                username=month_spec.username,
                email=f"{month_spec.username}@example.com",
                password=password,
            )
            users[month_spec.username] = user

        source, _ = DocumentSource.objects.get_or_create(
            user=user,
            name="Contract Fixture Sheets",
            defaults={"source_type": DocumentSource.SourceType.DIRECT_UPLOAD, "is_active": True},
        )
        legacy_entries = month_spec.legacy_entries
        document = Document.objects.create(
            user=user,
            source=source,
            month=month_spec.month,
            year=month_spec.year,
            total_expenses=month_spec.legacy_total(EntryKind.EXPENSE),
            total_income=month_spec.legacy_total(EntryKind.INCOME),
            expenses_count=sum(1 for entry in legacy_entries if entry.kind == EntryKind.EXPENSE),
            income_count=sum(1 for entry in legacy_entries if entry.kind == EntryKind.INCOME),
        )
        Transaction.objects.bulk_create(
            Transaction(
                document=document,
                transaction_type=entry.kind,
                date=entry.date,
                amount=entry.amount,
                description=entry.description,
                category=entry.category,
            )
            for entry in legacy_entries
        )
    return users
