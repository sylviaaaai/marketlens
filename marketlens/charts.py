"""Prepare chart data without exposing pandas interval objects to Streamlit."""
import pandas as pd


def price_distribution(prices: pd.Series) -> pd.DataFrame:
    """Count known prices by ascending interval, using plain text labels."""
    known_prices = prices.dropna()
    if known_prices.empty:
        return pd.DataFrame({
            "price_range": pd.Series(dtype="str"),
            "number_of_listings": pd.Series(dtype="int64"),
        })

    intervals = pd.cut(known_prices, bins=min(10, len(known_prices)), duplicates="drop")
    counts = known_prices.groupby(intervals, observed=True, sort=True).size()
    labels = [str(interval) for interval in counts.index]
    return pd.DataFrame({
        "price_range": pd.Categorical(labels, categories=labels, ordered=True),
        "number_of_listings": counts.to_numpy(),
    })
