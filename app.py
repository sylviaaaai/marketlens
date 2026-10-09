"""MarketLens V0.0: explore legitimately sourced product listing snapshots."""
import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

from marketlens.charts import price_distribution

DB = Path("marketlens.db")
st.set_page_config(page_title="MarketLens — Starter", layout="wide")
st.title("MarketLens | Product Explorer")
st.caption("V0.0: verified data import → database → searchable product comparison")

if not DB.exists():
    st.info("No data yet. First import a permitted CSV: python -m marketlens.ingest examples/demo_synthetic.csv")
    st.stop()

with sqlite3.connect(DB) as conn:
    df = pd.read_sql_query("""
        SELECT p.source, p.source_product_id, p.product_name, p.shop_name, p.product_url,
               p.category, s.observed_at, s.price, s.displayed_sales,
               s.sales_lower_bound, s.sales_is_exact, s.data_origin
          FROM products p JOIN snapshots s ON s.product_id=p.id
         WHERE s.id=(SELECT s2.id FROM snapshots s2 WHERE s2.product_id=p.id
                      ORDER BY s2.observed_at DESC, s2.id DESC LIMIT 1)
    """, conn)

if df.empty:
    st.info("The database exists but has no product snapshots yet.")
    st.stop()

if (df["data_origin"] == "synthetic_demo").any():
    st.warning("SYNTHETIC DEMO DATA PRESENT. These records are only for software testing, not market insights.")

q = st.text_input("Search product or category", placeholder="e.g. 穿戴甲")
if q:
    mask = df["product_name"].str.contains(q, case=False, na=False, regex=False) | df["category"].str.contains(q, case=False, na=False, regex=False)
    df = df[mask]

col1, col2 = st.columns(2)
col1.metric("Matching listings", len(df))
col2.metric("Distinct shops", df["shop_name"].nunique())
if df.empty:
    st.info("No matches.")
    st.stop()

st.subheader("Latest product snapshots")
st.dataframe(df[["product_name", "shop_name", "category", "price", "displayed_sales", "sales_is_exact", "observed_at", "data_origin", "product_url"]], use_container_width=True)
st.subheader("Price distribution")
distribution = price_distribution(df["price"])
if distribution.empty:
    st.info("No known prices to display.")
else:
    st.bar_chart(distribution, x="price_range", y="number_of_listings")
st.caption("'Displayed sales' is not verified order volume; rounded / '+' displays are not exact. Never sum lower bounds as verified sales.")
