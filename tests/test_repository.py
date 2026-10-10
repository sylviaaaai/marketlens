"""Read-only repository checks using temporary synthetic databases."""
from hashlib import sha256
from pathlib import Path
import sqlite3

import pandas as pd
import pytest

from marketlens.ingest import import_csv, init_db
from marketlens.repository import load_latest_listings

DEMO = Path(__file__).resolve().parents[1] / "examples/demo_synthetic.csv"
EXPECTED_COLUMNS = [
    "source", "source_product_id", "product_name", "shop_name", "product_url",
    "category", "observed_at", "price", "displayed_sales", "sales_lower_bound",
    "sales_is_exact", "data_origin",
]


def test_latest_snapshot_and_source_fields_are_read_without_writes(tmp_path):
    db = tmp_path / "synthetic # demo.db"
    import_csv(DEMO, db)
    with sqlite3.connect(db) as conn:
        product_id = conn.execute(
            "SELECT id FROM products WHERE source_product_id='P001'"
        ).fetchone()[0]
        # Insert newest first, then an older observation with a larger ID.
        for observed_at, price in [
            ("2026-10-10T20:00:00+08:00", 42.0),
            ("2026-10-09T20:00:00+08:00", 41.0),
        ]:
            conn.execute(
                "INSERT INTO snapshots(product_id, observed_at, price, displayed_sales, "
                "sales_lower_bound, sales_is_exact, data_origin) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (product_id, observed_at, price, "300+", 300, 0, "synthetic_demo"),
            )
        conn.execute("UPDATE products SET category=NULL, product_url=NULL WHERE id=?", (product_id,))

    before = sha256(db.read_bytes()).hexdigest()
    result = load_latest_listings(db)
    assert sha256(db.read_bytes()).hexdigest() == before
    assert list(result.columns) == EXPECTED_COLUMNS
    assert len(result) == 3
    assert result["source_product_id"].is_unique
    row = result.set_index("source_product_id").loc["P001"]
    assert row["price"] == 42.0
    assert row["observed_at"] == "2026-10-10T20:00:00+08:00"
    assert row["data_origin"] == "synthetic_demo"
    assert row["source"] == "synthetic_shop"
    assert row["displayed_sales"] == "300+"
    assert row["sales_lower_bound"] == 300
    assert row["sales_is_exact"] == 0
    assert row["category"] is None and row["product_url"] is None


def test_empty_database_retains_column_contract(tmp_path):
    db = tmp_path / "empty.db"
    with sqlite3.connect(db) as conn:
        init_db(conn)
    result = load_latest_listings(db)
    assert result.empty
    assert list(result.columns) == EXPECTED_COLUMNS


def test_missing_database_is_not_created(tmp_path):
    db = tmp_path / "missing.db"
    with pytest.raises(FileNotFoundError, match="database not found"):
        load_latest_listings(db)
    assert not db.exists()


def test_invalid_schema_is_reported_without_initializing_tables(tmp_path):
    db = tmp_path / "no_tables.db"
    with sqlite3.connect(db):
        pass
    before = db.read_bytes()
    with pytest.raises(pd.errors.DatabaseError, match="no such table"):
        load_latest_listings(db)
    assert db.read_bytes() == before
