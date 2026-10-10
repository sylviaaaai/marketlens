"""Read-only access to the existing V0.0 SQLite schema."""
from contextlib import closing
from pathlib import Path
import sqlite3

import pandas as pd


def load_latest_listings(db_path: str | Path) -> pd.DataFrame:
    """Return one latest snapshot per listing, with all legacy source fields.

    Preserve the existing observed_at text ordering, then snapshot ID ordering.
    An initialized empty database returns an empty frame with named columns.
    Missing files raise FileNotFoundError; invalid schemas raise pd.errors.DatabaseError.
    This function never creates a database, initializes tables, or writes records.
    """
    path = Path(db_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"MarketLens database not found: {path}")

    with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as conn:
        return pd.read_sql_query("""
            SELECT p.source, p.source_product_id, p.product_name, p.shop_name, p.product_url,
                   p.category, s.observed_at, s.price, s.displayed_sales,
                   s.sales_lower_bound, s.sales_is_exact, s.data_origin
              FROM products p JOIN snapshots s ON s.product_id=p.id
             WHERE s.id=(SELECT s2.id FROM snapshots s2 WHERE s2.product_id=p.id
                          ORDER BY s2.observed_at DESC, s2.id DESC LIMIT 1)
        """, conn)
