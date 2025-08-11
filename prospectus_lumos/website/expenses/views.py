from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponse
from django.views.decorators.http import require_http_methods
from django.core.paginator import Paginator
from django.db.models import Q
from collections import Counter

from prospectus_lumos.apps.accounts.models import DocumentSource
from prospectus_lumos.apps.documents.models import Document
from prospectus_lumos.apps.expenses.services import ExpenseSheetService, ExpenseAnalyzerService
from prospectus_lumos.core.constants import MONTHS_LIST
from prospectus_lumos.core.utils import TypedHttpRequest


def login_view(request: TypedHttpRequest) -> HttpResponse:
    """User login page"""
    if request.user.is_authenticated:
        return redirect("dashboard")

    if request.method == "POST":
        username = request.POST.get("username")
        password = request.POST.get("password")

        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            next_url = request.GET.get("next", "dashboard")
            return redirect(next_url)
        else:
            messages.error(request, "Invalid username or password")

    return render(request, "expenses/login.html")


def logout_view(request: TypedHttpRequest) -> HttpResponse:
    """User logout"""
    logout(request)
    messages.success(request, "You have been logged out successfully")
    return redirect("login")


@login_required
def dashboard_view(request: TypedHttpRequest) -> HttpResponse:
    """Dashboard showing overview of user's financial data"""
    # Get recent documents
    recent_docs = Document.objects.filter(user=request.user).order_by("-year", "-month")[:6]

    # Get document sources
    sources = DocumentSource.objects.filter(user=request.user, is_active=True)

    # Calculate summary statistics
    total_documents = Document.objects.filter(user=request.user).count()

    if recent_docs:
        total_income = sum(doc.total_income for doc in recent_docs)
        total_expenses = sum(doc.total_expenses for doc in recent_docs)
        net_income = total_income - total_expenses
    else:
        total_income = total_expenses = net_income = 0

    context = {
        "recent_documents": recent_docs,
        "document_sources": sources,
        "total_documents": total_documents,
        "total_income": total_income,
        "total_expenses": total_expenses,
        "net_income": net_income,
        "summary_period": "latest 6 months",
        "selected_tab": "dashboard",
    }

    return render(request, "expenses/dashboard.html", context)


@login_required
def document_list_view(request: TypedHttpRequest) -> HttpResponse:
    """List of saved CSV documents"""
    documents = Document.objects.filter(user=request.user).order_by("-year", "-month")

    # Search functionality
    search_query = request.GET.get("search", "")
    if search_query:
        documents = documents.filter(
            Q(google_sheet_name__icontains=search_query)
            | Q(month__icontains=search_query)
            | Q(year__icontains=search_query)
        )

    # Filter by year
    year_filter = request.GET.get("year", "")
    if year_filter:
        documents = documents.filter(year=year_filter)

    # Filter by month
    month_filter = request.GET.get("month", "")
    if month_filter:
        documents = documents.filter(month=month_filter)

    # Get available years and months for filter dropdown
    available_years = (
        Document.objects.filter(user=request.user).values_list("year", flat=True).distinct().order_by("-year")
    )
    available_months = MONTHS_LIST

    # Pagination
    paginator = Paginator(documents, 10)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    context = {
        "page_obj": page_obj,
        "search_query": search_query,
        "year_filter": year_filter,
        "month_filter": month_filter,
        "available_years": available_years,
        "available_months": available_months,
        "selected_tab": "documents",
    }

    return render(request, "expenses/document_list.html", context)


@login_required
def income_analyzer_view(request: TypedHttpRequest) -> HttpResponse:
    """Income analyzer page with filtering and statistics"""
    analyzer = ExpenseAnalyzerService(request.user)

    # Get filter parameters
    year_filter = request.GET.get("year", "")
    month_filter = request.GET.get("month", "")

    # Convert to integers if provided
    year = int(year_filter) if year_filter else None
    month = int(month_filter) if month_filter else None

    # Get analysis data
    analysis = analyzer.get_income_analysis(year=year, month=month)

    # Get available years and months for filter dropdown
    available_years = (
        Document.objects.filter(user=request.user).values_list("year", flat=True).distinct().order_by("-year")
    )
    available_months = MONTHS_LIST

    context = {
        "analysis": analysis,
        "year_filter": year_filter,
        "month_filter": month_filter,
        "available_years": available_years,
        "available_months": available_months,
        "filter_text": f"for {month_filter}/{year_filter}" if year_filter or month_filter else "for all periods",
        "selected_tab": "income",
    }

    return render(request, "expenses/income_analyzer.html", context)


@login_required
def expense_analyzer_view(request: TypedHttpRequest) -> HttpResponse:
    """Expense analyzer page with filtering and statistics"""
    analyzer = ExpenseAnalyzerService(request.user)

    # Get filter parameters
    year_filter = request.GET.get("year", "")
    month_filter = request.GET.get("month", "")

    # Convert to integers if provided
    year = int(year_filter) if year_filter else None
    month = int(month_filter) if month_filter else None

    # Get analysis data
    analysis = analyzer.get_expense_analysis(year=year, month=month)

    # Get available years and months for filter dropdown
    available_years = (
        Document.objects.filter(user=request.user).values_list("year", flat=True).distinct().order_by("-year")
    )
    available_months = MONTHS_LIST

    context = {
        "analysis": analysis,
        "year_filter": year_filter,
        "month_filter": month_filter,
        "available_years": available_years,
        "available_months": available_months,
        "filter_text": f"for {month_filter}/{year_filter}" if year_filter or month_filter else "for all periods",
        "selected_tab": "expenses",
    }

    return render(request, "expenses/expense_analyzer.html", context)


@login_required
@require_http_methods(["POST"])
def sync_documents_view(request: TypedHttpRequest) -> HttpResponse:
    """Sync documents from Google Drive sources"""
    try:
        service = ExpenseSheetService(request.user)

        # Get all active Google Drive sources for the user
        sources = DocumentSource.objects.filter(user=request.user, source_type="google_drive", is_active=True)

        total_processed = 0

        for source in sources:
            try:
                processed_docs = service.sync_google_drive_documents(source)
                total_processed += len(processed_docs)
            except Exception as e:
                messages.error(request, f"Error syncing {source.name}: {str(e)}")

        if total_processed > 0:
            messages.success(request, f"Successfully processed {total_processed} documents")
        else:
            messages.info(request, "No new documents found to process")

    except Exception as e:
        messages.error(request, f"Sync failed: {str(e)}")

    return redirect("dashboard")


@login_required
def download_csv_view(request: TypedHttpRequest, document_id: int) -> HttpResponse:
    """Download CSV file for a specific document"""
    document = get_object_or_404(Document, id=document_id, user=request.user)

    if not document.csv_file:
        messages.error(request, "CSV file not found for this document")
        return redirect("document_list")

    try:
        response = HttpResponse(document.csv_file.read(), content_type="text/csv")
        response["Content-Disposition"] = (
            f'attachment; filename="{document.user.username}_{document.year}_{document.month:02d}.csv"'
        )
        return response
    except Exception as e:
        messages.error(request, f"Error downloading file: {str(e)}")
        return redirect("document_list")


@login_required
@require_http_methods(["POST"])
def resync_document_view(request: TypedHttpRequest, document_id: int) -> HttpResponse:
    """Re-sync a single document from its Google Sheet, replacing transactions safely."""
    document = get_object_or_404(Document, id=document_id, user=request.user)
    try:
        service = ExpenseSheetService(request.user)
        service.resync_document(document)
        messages.success(request, f"Resynced {document.google_sheet_name or 'document'} successfully")
    except Exception as e:
        messages.error(request, f"Resync failed: {str(e)}")
    return redirect("document_list")


@login_required
def category_analyzer_view(request: TypedHttpRequest) -> HttpResponse:
    """Analyze a single category for income or expense with filters and insights"""
    category_type = request.GET.get("type", "expense")  # 'expense' or 'income'
    category_name = request.GET.get("category", "")
    year_filter = request.GET.get("year", "")
    month_filter = request.GET.get("month", "")

    documents = Document.objects.filter(user=request.user)
    if year_filter:
        documents = documents.filter(year=int(year_filter))
    if month_filter:
        documents = documents.filter(month=int(month_filter))

    # Prepare transactions for the selected category
    transactions_qs = []
    for doc in documents:
        qs = doc.transactions.filter(transaction_type=category_type)
        if category_name:
            qs = qs.filter(category=category_name)
        transactions_qs.extend(
            list(qs.values("description", "amount", "category", "date", "document__month", "document__year"))
        )

    # Trend by month-year
    trend: dict[str, float] = {}
    for t in transactions_qs:
        m = t["document__month"]
        y = t["document__year"]
        key = f"{y}-{m:02d}"
        trend.setdefault(key, 0)
        trend[key] += float(t["amount"])  # Decimal -> float for JS consumption

    # Frequent items by description
    desc_counts = Counter([t["description"] or "(No description)" for t in transactions_qs])
    top_items = desc_counts.most_common(10)

    # Top transaction by amount
    top_tx = None
    top_transactions = []
    if transactions_qs:
        top_tx = max(transactions_qs, key=lambda x: x["amount"])
        top_transactions = sorted(transactions_qs, key=lambda x: x["amount"], reverse=True)[:5]

    # Summary totals and averages
    transactions_count = len(transactions_qs)
    total_value = sum([t["amount"] for t in transactions_qs]) if transactions_qs else 0
    # number of distinct months in trend for average
    months_in_trend = len(trend.keys()) if trend else 0
    average_value = (total_value / months_in_trend) if months_in_trend else 0

    # Ordered trend arrays for chart (oldest -> newest)
    trend_labels = []
    trend_values = []
    if trend:
        ordered_keys = sorted(trend.keys())
        trend_labels = ordered_keys
        trend_values = [trend[k] for k in ordered_keys]

    # Available facets
    available_years = (
        Document.objects.filter(user=request.user).values_list("year", flat=True).distinct().order_by("-year")
    )
    available_months = MONTHS_LIST

    context = {
        "category_type": category_type,
        "category_name": category_name,
        "year_filter": year_filter,
        "month_filter": month_filter,
        "available_years": available_years,
        "available_months": available_months,
        "trend": trend,
        "trend_labels": trend_labels,
        "trend_values": trend_values,
        "top_items": top_items,
        "top_tx": top_tx,
        "top_transactions": top_transactions,
        "transactions_count": transactions_count,
        "total_value": total_value,
        "average_value": average_value,
        "selected_tab": "expenses" if category_type == "expense" else "income",
    }

    return render(request, "expenses/category_analyzer.html", context)
