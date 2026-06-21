from __future__ import annotations

import csv
import io
from typing import Any

import google.generativeai as genai


SYSTEM_PROMPT = """You are a financial analyst assistant for a personal finance tracker called Prospectus Lumos.
The user tracks their income and expenses monthly in Indonesian Rupiah (IDR).

Your task is to analyze the provided financial data and deliver clear, honest, and helpful insights.
The data will be provided in CSV format with columns: description, amount, category, transaction_type, year, month.

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


def prepare_data_as_text(documents: Any, analyzer_type: str) -> str:
    """Convert document queryset into a CSV text format suitable for AI analysis.

    Args:
        documents: A queryset or list of Document objects with prefetched transactions.
        analyzer_type: One of 'income', 'expense', 'portfolio'.

    Returns:
        CSV-formatted text string.
    """
    output = io.StringIO()
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
