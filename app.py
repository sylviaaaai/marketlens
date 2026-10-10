"""MarketLens V0.0: explore legitimately sourced product listing snapshots."""
from pathlib import Path

import streamlit as st

from marketlens.product_service import has_synthetic_data, search_products
from marketlens.repository import load_latest_listings

DB = Path("marketlens.db")
st.set_page_config(page_title="MarketLens — Starter", layout="wide")
st.title("MarketLens | Product Explorer")
st.caption("V0.0: verified data import → database → searchable product comparison")

try:
    listings = load_latest_listings(DB)
except FileNotFoundError:
    st.info("No data yet. First import a permitted CSV: python -m marketlens.ingest examples/demo_synthetic.csv")
    st.stop()

if listings.empty:
    st.info("The database exists but has no product snapshots yet.")
    st.stop()

if has_synthetic_data(listings):
    st.warning("SYNTHETIC DEMO DATA PRESENT. These records are only for software testing, not market insights.")

q = st.text_input("Search product or category", placeholder="e.g. 穿戴甲")
result = search_products(listings, q)

col1, col2 = st.columns(2)
col1.metric("Matching listings", result.matching_listings)
col2.metric("Distinct shops", result.distinct_shops)
if result.listings.empty:
    st.info("No matches.")
    st.stop()

st.subheader("Latest product snapshots")
st.dataframe(result.listings[["product_name", "shop_name", "category", "price", "displayed_sales", "sales_is_exact", "observed_at", "data_origin", "product_url"]], use_container_width=True)
st.subheader("Price distribution")
distribution = result.price_distribution
if distribution.empty:
    st.info("No known prices to display.")
else:
    st.bar_chart(distribution, x="price_range", y="number_of_listings")
st.caption("'Displayed sales' is not verified order volume; rounded / '+' displays are not exact. Never sum lower bounds as verified sales.")
