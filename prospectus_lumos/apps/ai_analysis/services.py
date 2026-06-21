from __future__ import annotations

import csv
import io
from typing import Any

from openai import OpenAI


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
- Format your response in plain text with clear section headings

Do NOT:
- Make up data you cannot see
- Give financial advice that requires licensing
- Share overly generic insights that could apply to anyone
"""


def get_ai_insights(api_token: str, model: str, data_text: str) -> str:
    """Send financial data to the AI model and return analysis insights.

    Args:
        api_token: The OpenAI or Anthropic API key.
        model: The model identifier (e.g. 'gpt-4o', 'claude-3-sonnet-20240229').
        data_text: The financial data in CSV text format.

    Returns:
        The AI-generated insights as a string.

    Raises:
        openai.OpenAIError: If the API call fails.
    """
    if model.startswith("claude-"):
        return _call_anthropic(api_token, model, data_text)
    return _call_openai(api_token, model, data_text)


def _call_openai(api_token: str, model: str, data_text: str) -> str:
    client = OpenAI(api_key=api_token)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Here is the financial data to analyze:\n\n{data_text}"},
        ],
    )
    return response.choices[0].message.content or ""


def _call_anthropic(api_token: str, model: str, data_text: str) -> str:
    client = OpenAI(
        base_url="https://api.anthropic.com/v1/",
        api_key=api_token,
    )
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Here is the financial data to analyze:\n\n{data_text}"},
        ],
        extra_body={
            "max_tokens": 4096,
        },
    )
    return response.choices[0].message.content or ""


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
