import csv
import io
import re
from typing import List, Tuple, Dict, Any
from decimal import Decimal

from django.core.files.base import ContentFile
from django.utils import timezone

from libraries.google_cloud.backends import GoogleDriveBackend
from prospectus_lumos.apps.accounts.models import DocumentSource
from prospectus_lumos.apps.documents.models import Document
from prospectus_lumos.apps.transactions.models import Transaction


class ExpenseSheetService:
    """Service to handle Google Drive sheet parsing and CSV creation"""

    def __init__(self, user: Any) -> None:
        self.user = user

    def sync_google_drive_documents(self, source: DocumentSource) -> List[Document]:
        """Sync documents from Google Drive source"""
        if source.source_type != "google_drive" or not source.google_credentials:
            raise ValueError("Source must be Google Drive with valid credentials")

        # Initialize Google Drive backend
        creds_path = source.google_credentials.service_account_file.path
        backend = GoogleDriveBackend(creds_path)

        # List monthly budget files
        budget_files = backend.list_monthly_budget_files()

        processed_documents = []

        for file in budget_files:
            try:
                # Parse the file name to extract month and year
                month, year = self._extract_month_year(file.name)
                if not month or not year:
                    continue

                # Check if document already exists
                existing_doc = Document.objects.filter(user=self.user, month=month, year=year).first()

                # Skip if document exists and has same Google Sheet ID
                if existing_doc and existing_doc.google_sheet_id == file.key:
                    continue

                # Parse the Google Sheet
                expenses, income = backend.parse_monthly_budget_sheet(file.key)

                # Create CSV content
                csv_content = self._create_csv_content(expenses, income)

                # Calculate totals
                total_expenses = sum(Decimal(str(exp["amount"])) for exp in expenses)
                total_income = sum(Decimal(str(inc["amount"])) for inc in income)

                # Create or update document
                if existing_doc:
                    document = existing_doc
                    # Delete old transactions
                    document.transactions.all().delete()
                else:
                    document = Document(user=self.user, source=source, month=month, year=year)

                # Update document fields
                document.google_sheet_id = file.key
                document.google_sheet_name = file.name
                document.total_expenses = total_expenses
                document.total_income = total_income
                document.expenses_count = len(expenses)
                document.income_count = len(income)

                # Save CSV file
                csv_filename = f"{self.user.username}_{year}_{month:02d}.csv"
                document.csv_file.save(csv_filename, ContentFile(csv_content.encode("utf-8")), save=False)

                document.save()

                # Create transaction records
                self._create_transaction_records(document, expenses, income)

                processed_documents.append(document)

            except Exception as e:
                # Log error but continue processing other files
                print(f"Error processing file {file.name}: {str(e)}")
                continue

        # Update last sync time
        source.last_sync = timezone.now()
        source.save()

        return processed_documents

    def _extract_month_year(self, filename: str) -> Tuple[int, int]:
        """Extract month and year from filename like 'Monthly budget Jan 2025'"""
        months = {
            "jan": 1,
            "january": 1,
            "feb": 2,
            "february": 2,
            "mar": 3,
            "march": 3,
            "apr": 4,
            "april": 4,
            "may": 5,
            "jun": 6,
            "june": 6,
            "jul": 7,
            "july": 7,
            "aug": 8,
            "august": 8,
            "sep": 9,
            "september": 9,
            "oct": 10,
            "october": 10,
            "nov": 11,
            "november": 11,
            "dec": 12,
            "december": 12,
        }

        filename_lower = filename.lower()

        # Find year (4-digit number)
        year_match = re.search(r"\b(20\d{2})\b", filename_lower)
        year = int(year_match.group(1)) if year_match else None

        # Find month
        month = None
        for month_name, month_num in months.items():
            if month_name in filename_lower:
                month = month_num
                break

        return month, year

    def _create_csv_content(self, expenses: List[Dict[str, Any]], income: List[Dict[str, Any]]) -> str:
        """Create CSV content from expenses and income data"""
        output = io.StringIO()
        writer = csv.writer(output)

        # Write header
        writer.writerow(["name", "amount", "description", "category", "expense/income"])

        # Write expenses
        for expense in expenses:
            writer.writerow(
                [
                    expense.get("description", ""),
                    expense.get("amount", 0),
                    expense.get("description", ""),
                    expense.get("category", ""),
                    "expense",
                ]
            )

        # Write income
        for inc in income:
            writer.writerow(
                [
                    inc.get("description", ""),
                    inc.get("amount", 0),
                    inc.get("description", ""),
                    inc.get("category", ""),
                    "income",
                ]
            )

        return output.getvalue()

    def _create_transaction_records(
        self, document: Document, expenses: List[Dict[str, Any]], income: List[Dict[str, Any]]
    ) -> None:
        """Create transaction records from parsed data"""
        transactions = []

        # Create expense transactions
        for expense in expenses:
            transactions.append(
                Transaction(
                    document=document,
                    transaction_type="expense",
                    date=expense.get("date", ""),
                    amount=Decimal(str(expense.get("amount", 0))),
                    description=expense.get("description", ""),
                    category=expense.get("category", ""),
                )
            )

        # Create income transactions
        for inc in income:
            transactions.append(
                Transaction(
                    document=document,
                    transaction_type="income",
                    date=inc.get("date", ""),
                    amount=Decimal(str(inc.get("amount", 0))),
                    description=inc.get("description", ""),
                    category=inc.get("category", ""),
                )
            )

        # Bulk create transactions
        Transaction.objects.bulk_create(transactions)

    def resync_document(self, document: Document) -> Document:
        """Re-sync a single Google Drive-backed document.

        Re-parses the Google Sheet, re-generates the CSV, updates totals/counters,
        and replaces transactions to avoid duplicates.
        """
        source = document.source
        if source.source_type != "google_drive" or not source.google_credentials:
            raise ValueError("Document source must be Google Drive with valid credentials")

        if not document.google_sheet_id:
            raise ValueError("Document is missing google_sheet_id; cannot resync")

        creds_path = source.google_credentials.service_account_file.path
        backend = GoogleDriveBackend(creds_path)

        # Parse sheet
        expenses, income = backend.parse_monthly_budget_sheet(document.google_sheet_id)

        # Create CSV content
        csv_content = self._create_csv_content(expenses, income)

        # Calculate totals
        total_expenses = sum(Decimal(str(exp["amount"])) for exp in expenses)
        total_income = sum(Decimal(str(inc["amount"])) for inc in income)

        # Replace transactions and update document
        document.transactions.all().delete()
        document.total_expenses = total_expenses
        document.total_income = total_income
        document.expenses_count = len(expenses)
        document.income_count = len(income)

        csv_filename = f"{document.user.username}_{document.year}_{document.month:02d}.csv"
        document.csv_file.save(csv_filename, ContentFile(csv_content.encode("utf-8")), save=False)
        document.save()

        self._create_transaction_records(document, expenses, income)

        # Update last sync for the source for auditing
        source.last_sync = timezone.now()
        source.save(update_fields=["last_sync"])

        return document


class ExpenseAnalyzerService:
    """Service to analyze expenses and income data"""

    def __init__(self, user: Any) -> None:
        self.user = user

    def get_income_analysis(
        self,
        year: int | None = None,
        month: int | None = None,
        exclude_categories: list[str] | None = None,
    ) -> Dict[str, Any]:
        """Get income analysis for specified period, optionally excluding categories."""
        documents = Document.objects.filter(user=self.user)

        if year:
            documents = documents.filter(year=year)
        if month:
            documents = documents.filter(month=month)

        exclude_set = set(exclude_categories) if exclude_categories else set()

        # Group by category (skip excluded)
        income_by_category: Dict[str, Dict[str, Any]] = {}
        for doc in documents:
            income_transactions = doc.transactions.filter(transaction_type="income")
            for transaction in income_transactions:
                category = transaction.category or "Uncategorized"
                if category in exclude_set:
                    continue
                if category not in income_by_category:
                    income_by_category[category] = {"total": Decimal("0"), "count": 0, "average": Decimal("0")}
                income_by_category[category]["total"] += transaction.amount
                income_by_category[category]["count"] += 1

        # Calculate averages for categories
        for category_data in income_by_category.values():
            category_data["average"] = (
                category_data["total"] / category_data["count"] if category_data["count"] else Decimal("0")
            )

        total_income: Decimal = sum((cat["total"] for cat in income_by_category.values()), Decimal("0"))
        document_count: int = documents.count()
        average_income: Decimal | int = total_income / document_count if document_count > 0 else 0

        return {
            "total_income": total_income,
            "average_income": average_income,
            "document_count": document_count,
            "income_by_category": income_by_category,
            "documents": documents.order_by("-year", "-month"),
        }

    def get_expense_analysis(
        self,
        year: int | None = None,
        month: int | None = None,
        exclude_categories: list[str] | None = None,
    ) -> Dict[str, Any]:
        """Get expense analysis for specified period, optionally excluding categories."""
        documents = Document.objects.filter(user=self.user)

        if year:
            documents = documents.filter(year=year)
        if month:
            documents = documents.filter(month=month)

        exclude_set = set(exclude_categories) if exclude_categories else set()

        # Group by category (skip excluded)
        expenses_by_category: Dict[str, Dict[str, Any]] = {}
        for doc in documents:
            expense_transactions = doc.transactions.filter(transaction_type="expense")
            for transaction in expense_transactions:
                category = transaction.category or "Uncategorized"
                if category in exclude_set:
                    continue
                if category not in expenses_by_category:
                    expenses_by_category[category] = {"total": Decimal("0"), "count": 0, "average": Decimal("0")}
                expenses_by_category[category]["total"] += transaction.amount
                expenses_by_category[category]["count"] += 1

        # Calculate averages for categories
        for category_data in expenses_by_category.values():
            category_data["average"] = (
                category_data["total"] / category_data["count"] if category_data["count"] else Decimal("0")
            )

        total_expenses: Decimal = sum((cat["total"] for cat in expenses_by_category.values()), Decimal("0"))
        document_count: int = documents.count()
        average_expenses: Decimal | int = total_expenses / document_count if document_count > 0 else 0

        return {
            "total_expenses": total_expenses,
            "average_expenses": average_expenses,
            "document_count": document_count,
            "expenses_by_category": expenses_by_category,
            "documents": documents.order_by("-year", "-month"),
        }

    def get_portfolio_analysis(self, start_year: int | None = None, end_year: int | None = None) -> Dict[str, Any]:
        """Combined income + expense view with savings-focused statistics over a year range."""
        documents = Document.objects.filter(user=self.user)

        if start_year is not None:
            documents = documents.filter(year__gte=start_year)
        if end_year is not None:
            documents = documents.filter(year__lte=end_year)

        documents = documents.order_by("year", "month")

        document_count: int = documents.count()

        # Build month-by-month portfolio timeline and aggregate statistics in a single pass
        monthly_series: list[dict[str, Any]] = []
        positive_months = 0
        negative_months = 0
        zero_months = 0
        best_month: dict[str, Any] | None = None
        worst_month: dict[str, Any] | None = None
        savings_rates: list[Decimal] = []

        total_income: Decimal = Decimal("0")
        total_expenses: Decimal = Decimal("0")

        # Streaks of positive savings (net > 0)
        current_positive_streak = 0
        longest_positive_streak = 0

        for doc in documents:
            income = doc.total_income
            expenses = doc.total_expenses
            net = income - expenses
            # Store savings rate as a percentage (0-100) for easier display
            savings_rate = (net / income * 100) if income > 0 else Decimal("0")

            total_income += income
            total_expenses += expenses

            # Track counts
            if net > 0:
                positive_months += 1
                current_positive_streak += 1
                longest_positive_streak = max(longest_positive_streak, current_positive_streak)
            elif net < 0:
                negative_months += 1
                current_positive_streak = 0
            else:
                zero_months += 1
                current_positive_streak = 0

            savings_rates.append(savings_rate)

            entry = {
                "year": doc.year,
                "month": doc.month,
                "month_name": doc.month_name,
                "income": income,
                "expenses": expenses,
                "net": net,
                "savings_rate": savings_rate,
                "income_count": doc.income_count,
                "expenses_count": doc.expenses_count,
            }
            monthly_series.append(entry)

            if best_month is None or net > best_month["net"]:
                best_month = entry
            if worst_month is None or net < worst_month["net"]:
                worst_month = entry

        net_income: Decimal = total_income - total_expenses

        average_income: Decimal | int = total_income / document_count if document_count > 0 else 0
        average_expenses: Decimal | int = total_expenses / document_count if document_count > 0 else 0
        average_net_income: Decimal | int = net_income / document_count if document_count > 0 else 0

        # Aggregate savings-rate statistics
        overall_savings_rate: Decimal | int = (net_income / total_income * 100) if total_income > 0 else 0
        average_savings_rate: Decimal | int = (
            sum(savings_rates, Decimal("0")) / len(savings_rates) if savings_rates else 0
        )

        # Simple volatility measure: average absolute month-to-month net change
        volatility: Decimal | int = 0
        if len(monthly_series) > 1:
            deltas: list[Decimal] = []
            prev_net: Decimal = monthly_series[0]["net"]
            for entry in monthly_series[1:]:
                current_net: Decimal = entry["net"]  # type: ignore
                deltas.append(abs(current_net - prev_net))
                prev_net = current_net
            volatility = sum(deltas, Decimal("0")) / len(deltas) if deltas else 0

        # Top-level composition for quick insight
        total_flow = total_income + total_expenses
        income_share: Decimal | int = (total_income / total_flow * 100) if total_flow > 0 else 0
        expense_share: Decimal | int = (total_expenses / total_flow * 100) if total_flow > 0 else 0

        return {
            "total_income": total_income,
            "total_expenses": total_expenses,
            "net_income": net_income,
            "average_income": average_income,
            "average_expenses": average_expenses,
            "average_net_income": average_net_income,
            "document_count": document_count,
            "overall_savings_rate": overall_savings_rate,
            "average_savings_rate": average_savings_rate,
            "positive_months": positive_months,
            "negative_months": negative_months,
            "zero_months": zero_months,
            "best_month": best_month,
            "worst_month": worst_month,
            "longest_positive_streak": longest_positive_streak,
            "monthly_series": monthly_series,
            "volatility": volatility,
            "income_share": income_share,
            "expense_share": expense_share,
        }
