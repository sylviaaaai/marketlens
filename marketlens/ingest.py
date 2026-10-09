"""Permission-aware CSV ingestion with source provenance and append-only snapshots."""
from __future__ import annotations

import csv
import re
import sqlite3
from datetime import datetime
from pathlib import Path

REQUIRED = {
    "source", "source_product_id", "product_name", "shop_name",
    "price", "displayed_sales", "observed_at", "data_origin",
}
ALLOWED_ORIGINS = {"merchant_authorized", "official_api", "licensed_source", "synthetic_demo"}
SALES_PATTERN = re.compile(r"^(?:已售\s*)?(\d+(?:\.\d+)?)\s*(万|千|k|K)?\s*(\+|以上)?(?:\s*件)?$")


def parse_displayed_sales(raw: str) -> tuple[int | None, bool]:
    """Return (reported lower bound, is_exact). Never treat '300+' as 300 exact sales."""
    s = (raw or "").strip().replace(",", "")
    if not s:
        return None, False
    match = SALES_PATTERN.fullmatch(s)
    if not match:
        return None, False
    n, scale, qualifier = match.groups()
    multiplier = {None: 1, "万": 10000, "千": 1000, "k": 1000, "K": 1000}[scale]
    value = int(float(n) * multiplier)
    # Values with scale or '+' are conservatively treated as rounded / lower bounds.
    exact = scale is None and qualifier is None and float(n).is_integer()
    return value, exact


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY,
        source TEXT NOT NULL,
        source_product_id TEXT NOT NULL,
        product_name TEXT NOT NULL,
        shop_name TEXT NOT NULL,
        product_url TEXT,
        category TEXT,
        UNIQUE(source, source_product_id)
    );
    CREATE TABLE IF NOT EXISTS snapshots (
        id INTEGER PRIMARY KEY,
        product_id INTEGER NOT NULL REFERENCES products(id),
        observed_at TEXT NOT NULL,
        price REAL NOT NULL CHECK(price >= 0),
        displayed_sales TEXT,
        sales_lower_bound INTEGER,
        sales_is_exact INTEGER NOT NULL,
        data_origin TEXT NOT NULL,
        UNIQUE(product_id, observed_at)
    );
    """)


def import_csv(csv_path: str | Path, db_path: str | Path = "marketlens.db") -> tuple[int, int]:
    """Import permitted records. Returns (new_products, new_snapshots)."""
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        missing = REQUIRED - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Missing required columns: {', '.join(sorted(missing))}")
        rows = list(reader)

    validated = []
    for lineno, row in enumerate(rows, start=2):
        origin = row["data_origin"].strip()
        if origin not in ALLOWED_ORIGINS:
            raise ValueError(f"Line {lineno}: invalid data_origin {origin!r}")
        if not all((row[key] or "").strip() for key in ["source", "source_product_id", "product_name", "shop_name"]):
            raise ValueError(f"Line {lineno}: missing source / id / product / shop")
        try:
            price = float(row["price"])
            observed_at = datetime.fromisoformat(row["observed_at"].strip())
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Line {lineno}: invalid price or observed_at") from exc
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError(f"Line {lineno}: observed_at must include a timezone offset")
        if price < 0 or price != price or price == float('inf') or price == float('-inf'):
            raise ValueError(f"Line {lineno}: price must be finite and nonnegative")
        sales, exact = parse_displayed_sales(row.get("displayed_sales", ""))
        validated.append((row, price, observed_at.isoformat(), sales, exact, origin))

    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        init_db(conn)
        before_products = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        before_snapshots = conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
        for row, price, observed, sales, exact, origin in validated:
            conn.execute("""
                INSERT INTO products(source, source_product_id, product_name, shop_name, product_url, category)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, source_product_id) DO UPDATE SET
                    product_name=excluded.product_name,
                    shop_name=excluded.shop_name,
                    product_url=excluded.product_url,
                    category=excluded.category
            """, (row["source"].strip(), row["source_product_id"].strip(), row["product_name"].strip(),
                  row["shop_name"].strip(), (row.get("product_url") or "").strip(),
                  (row.get("category") or "").strip()))
            product_id = conn.execute("SELECT id FROM products WHERE source=? AND source_product_id=?",
                (row["source"].strip(), row["source_product_id"].strip())).fetchone()[0]
            conn.execute("""
                INSERT OR IGNORE INTO snapshots(product_id, observed_at, price, displayed_sales,
                                                sales_lower_bound, sales_is_exact, data_origin)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (product_id, observed, price, (row.get("displayed_sales") or "").strip(),
                  sales, int(exact), origin))
        after_products = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        after_snapshots = conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
    return after_products - before_products, after_snapshots - before_snapshots


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Import authorized listing data")
    parser.add_argument("csv_path")
    parser.add_argument("--db", default="marketlens.db")
    args = parser.parse_args()
    p, s = import_csv(args.csv_path, args.db)
    print(f"Imported {p} new products and {s} new snapshots into {args.db}")
