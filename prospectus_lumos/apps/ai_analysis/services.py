from __future__ import annotations

import csv
import io
from decimal import Decimal
from typing import Any

import google.generativeai as genai


SYSTEM_PROMPT = """You are a financial analyst assistant for a personal finance tracker called Prospectus Lumos.
The user tracks their income and expenses monthly in Indonesian Rupiah (IDR).

Your task is to analyze the provided financial data and deliver clear, honest, and helpful insights.
The data includes a pre-computed SUMMARY section with accurate totals computed by the application,
followed by the raw transaction records in CSV format.

**CRITICAL**: When reporting total amounts (total expenses, total income, category subtotals, etc.),
always use the **pre-computed values from the SUMMARY section**. Do NOT attempt to sum the raw
transaction rows yourself — the totals in the summary are guaranteed to be accurate.

The raw CSV columns are: description, amount, category, transaction_type, year, month.

You MUST:
- Explain noticeable trends (increasing spending in certain categories, income growth, etc.)
- Point out unusual activity or anomalies (spikes, unexpected drops, one-off large transactions)
- Suggest what can be cut back or optimized
- Share one or two interesting or fun facts about the spending patterns
- Provide actionable, truthful insights — never make up data or patterns that are not present
- Keep your analysis concise but thorough
- Use Indonesian Rupiah (Rp) when mentioning amounts
- Respond in English regardless of the language used in the data
- Format your response in well-structured Markdown with clear headings, bullet points, and emphasis

Do NOT:
- Make up data you cannot see
- Calculate your own totals — always reference the SUMMARY section for aggregate numbers
- Give financial advice that requires licensing
- Share overly generic insights that could apply to anyone
"""


def get_ai_insights(api_token: str, model: str, data_text: str) -> str:
    """Send financial data to Google Gemini and return analysis insights.

    Args:
        api_token: The Google Gemini API key.
        model: The model identifier (e.g. 'gemini-2.5-flash').
        data_text: The financial data in CSV text format.

    Returns:
        The AI-generated insights as a string.
    """
    genai.configure(api_key=api_token)
    gemini_model = genai.GenerativeModel(model)
    response = gemini_model.generate_content(
        f"{SYSTEM_PROMPT}\n\nHere is the financial data to analyze:\n\n{data_text}",
    )
    return response.text or ""


def _build_summary_lines(documents: Any, analyzer_type: str) -> list[str]:
    """Build pre-computed summary lines that the AI can safely reference.

    Returns a list of summary lines (one per line) to be prepended to the raw CSV data.
    """
    lines: list[str] = []

    transaction_type_filter: str | None = None
    if analyzer_type == "income":
        transaction_type_filter = "income"
    elif analyzer_type == "expense":
        transaction_type_filter = "expense"

    total_amount = Decimal("0")
    category_totals: dict[str, Decimal] = {}
    transaction_count = 0
    months_covered: set[str] = set()

    for doc in documents:
        transactions = doc.transactions.all()
        if transaction_type_filter:
            transactions = transactions.filter(transaction_type=transaction_type_filter)

        for tx in transactions:
            amount = tx.amount if isinstance(tx.amount, Decimal) else Decimal(str(tx.amount))
            total_amount += amount
            transaction_count += 1
            cat = tx.category or "Uncategorized"
            category_totals[cat] = category_totals.get(cat, Decimal("0")) + amount
            months_covered.add(f"{doc.year}-{doc.month:02d}")

    total_formatted = f"Rp {total_amount:,.0f}"
    lines.append("=== PRE-COMPUTED SUMMARY (accurate, use these values) ===")
    lines.append(f"Analyzer type: {analyzer_type}")
    lines.append(f"Total amount: {total_formatted}")
    lines.append(f"Transaction count: {transaction_count}")
    lines.append(f"Documents (months) covered: {len(months_covered)}")

    if category_totals:
        lines.append("Category subtotals:")
        sorted_categories = sorted(category_totals.items(), key=lambda x: x[1], reverse=True)
        for cat, subtotal in sorted_categories:
            share_pct = (subtotal / total_amount * 100) if total_amount > 0 else Decimal("0")
            lines.append(f"  - {cat}: Rp {subtotal:,.0f} ({share_pct:.1f}%)")

    lines.append("=== END SUMMARY ===\n")
    return lines


def prepare_data_as_text(documents: Any, analyzer_type: str) -> str:
    """Convert document queryset into a CSV text format suitable for AI analysis.

    Includes a pre-computed SUMMARY section with accurate totals so the AI
    does not try to perform arithmetic on raw rows (which LLMs are unreliable at).

    Args:
        documents: A queryset or list of Document objects with prefetched transactions.
        analyzer_type: One of 'income', 'expense', 'portfolio'.

    Returns:
        Text string with pre-computed summary followed by CSV data.
    """
    summary_lines = _build_summary_lines(documents, analyzer_type)

    output = io.StringIO()
    for line in summary_lines:
        output.write(line + "\n")

    writer = csv.writer(output)
    writer.writerow(["description", "amount", "category", "transaction_type", "year", "month"])

    for doc in documents:
        transaction_type_filter = None
        if analyzer_type == "income":
            transaction_type_filter = "income"
        elif analyzer_type == "expense":
            transaction_type_filter = "expense"

        transactions = doc.transactions.all()
        if transaction_type_filter:
            transactions = transactions.filter(transaction_type=transaction_type_filter)

        for tx in transactions:
            writer.writerow(
                [
                    tx.description,
                    str(tx.amount),
                    tx.category,
                    tx.transaction_type,
                    doc.year,
                    doc.month,
                ]
            )

    return output.getvalue()
