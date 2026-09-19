"""Read-only baseline report of the legacy monthly income and expense totals.

The canonical ledger migration has to prove it reproduces today's numbers, so
this command records what the legacy Google-Sheet models currently hold, per
user and per month, before any migration runs. It never writes.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from django.core.management.base import BaseCommand
from django.db.models import Count, Sum

from prospectus_lumos.apps.documents.models import Document
from prospectus_lumos.apps.transactions.models import Transaction

MonthKey = tuple[str, int, int]


class Command(BaseCommand):
    help = "Report current legacy monthly income/expense totals per user (read-only)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--user", type=str, default="", help="Limit the report to a single username.")
        parser.add_argument(
            "--format",
            choices=["text", "json"],
            default="text",
            help="Output format (default: text).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        username: str = options["user"]
        months = self._collect(username)
        if options["format"] == "json":
            self.stdout.write(json.dumps({"months": months}, indent=2))
            return
        self._write_text(months)

    def _collect(self, username: str) -> list[dict[str, Any]]:
        """Build one report row per user/month from the legacy documents and transactions."""
        documents = Document.objects.select_related("user").order_by("user__username", "year", "month")
        transactions = Transaction.objects.all()
        if username:
            documents = documents.filter(user__username=username)
            transactions = transactions.filter(document__user__username=username)

        rows: dict[MonthKey, dict[str, Any]] = {}
        for document in documents:
            rows[(document.user.username, document.year, document.month)] = {
                "username": document.user.username,
                "year": document.year,
                "month": document.month,
                "income": Decimal("0"),
                "expenses": Decimal("0"),
                "income_count": 0,
                "expense_count": 0,
                "document_income": document.total_income,
                "document_expenses": document.total_expenses,
            }

        aggregates = (
            transactions.values("document__user__username", "document__year", "document__month", "transaction_type")
            .annotate(total=Sum("amount"), entries=Count("id"))
            .order_by()
        )
        for aggregate in aggregates:
            key: MonthKey = (
                aggregate["document__user__username"],
                aggregate["document__year"],
                aggregate["document__month"],
            )
            row = rows.get(key)
            if row is None:
                continue
            if aggregate["transaction_type"] == Transaction.TransactionType.INCOME:
                row["income"] = aggregate["total"]
                row["income_count"] = aggregate["entries"]
            elif aggregate["transaction_type"] == Transaction.TransactionType.EXPENSE:
                row["expenses"] = aggregate["total"]
                row["expense_count"] = aggregate["entries"]

        report: list[dict[str, Any]] = []
        for row in sorted(rows.values(), key=lambda item: (item["username"], item["year"], item["month"])):
            income: Decimal = row["income"]
            expenses: Decimal = row["expenses"]
            report.append(
                {
                    "username": row["username"],
                    "year": row["year"],
                    "month": row["month"],
                    "income": str(income),
                    "expenses": str(expenses),
                    "net": str(income - expenses),
                    "income_count": row["income_count"],
                    "expense_count": row["expense_count"],
                    "document_income": str(row["document_income"]),
                    "document_expenses": str(row["document_expenses"]),
                    "matches_document_totals": (
                        income == row["document_income"] and expenses == row["document_expenses"]
                    ),
                }
            )
        return report

    def _write_text(self, months: list[dict[str, Any]]) -> None:
        if not months:
            self.stdout.write("No legacy documents found.")
            return
        self.stdout.write(f"{'user':<20}{'period':<10}{'income':>18}{'expenses':>18}{'net':>18}  drift")
        for month in months:
            period = f"{month['year']}-{month['month']:02d}"
            drift = "" if month["matches_document_totals"] else "MISMATCH"
            self.stdout.write(
                f"{month['username']:<20}{period:<10}{month['income']:>18}"
                f"{month['expenses']:>18}{month['net']:>18}  {drift}"
            )
        self.stdout.write(f"{len(months)} user-month rows reported.")
