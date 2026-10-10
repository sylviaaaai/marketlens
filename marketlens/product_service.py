"""Reusable listing search and statistics, independent of storage and UI."""
from dataclasses import dataclass

import pandas as pd

from marketlens.charts import price_distribution


@dataclass
class ProductSearchResult:
    """Filtered listing rows, counts, and chart data (not a JSON response).

    matching_listings counts rows, not canonical consumer products.
    distinct_shops counts distinct non-null shop names, as in the legacy app.
    price_distribution contains price_range and number_of_listings columns.
    """

    listings: pd.DataFrame
    matching_listings: int
    distinct_shops: int
    price_distribution: pd.DataFrame


def has_synthetic_data(listings: pd.DataFrame) -> bool:
    """Inspect the full catalog before filtering so warnings stay visible."""
    if "data_origin" not in listings.columns:
        raise ValueError("Missing required column: data_origin")
    return bool(listings["data_origin"].eq("synthetic_demo").any())


def search_products(listings: pd.DataFrame, query: str = "") -> ProductSearchResult:
    """Search names/categories using case-insensitive, literal substrings.

    Input is the repository's listing frame; at least product_name, category,
    shop_name and price are required. Prices must be numeric or missing.
    Empty query returns all rows; whitespace is literal (no automatic stripping).
    Null names/categories do not match. The caller's frame is not modified.
    """
    required = {"product_name", "category", "shop_name", "price"}
    missing = required - set(listings.columns)
    if missing:
        raise ValueError(f"Missing required columns: {', '.join(sorted(missing))}")

    if query:
        mask = (
            listings["product_name"].astype("string").str.contains(
                query, case=False, na=False, regex=False
            )
            | listings["category"].astype("string").str.contains(
                query, case=False, na=False, regex=False
            )
        )
        matches = listings.loc[mask].copy()
    else:
        matches = listings.copy()

    return ProductSearchResult(
        listings=matches,
        matching_listings=len(matches),
        distinct_shops=int(matches["shop_name"].nunique()),
        price_distribution=price_distribution(matches["price"]),
    )
