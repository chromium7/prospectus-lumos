def category_key(category: str) -> str:
    """Return the case-preserving category key, with blank labels grouped as Uncategorized."""

    return category.strip() or "Uncategorized"
