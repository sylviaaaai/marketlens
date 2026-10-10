"""Pure service tests: all values below are synthetic software fixtures."""
import subprocess
import sys

import pandas as pd
import pytest

from marketlens.product_service import has_synthetic_data, search_products


@pytest.fixture
def listings():
    return pd.DataFrame({
        "source_product_id": ["demo-1", "demo-2", "demo-3", "demo-4"],
        "product_name": ["Demo Cream [A]", "demo Balm", "演示面霜", None],
        "category": ["face", "face", "护肤", None],
        "shop_name": ["Demo shop", "Demo shop", "Demo other", None],
        "price": [20.0, 40.0, 60.0, None],
        "data_origin": ["synthetic_demo"] * 4,
    })


@pytest.mark.parametrize("query, expected_ids, expected_shops", [
    ("", ["demo-1", "demo-2", "demo-3", "demo-4"], 2),
    ("CREAM", ["demo-1"], 1),
    ("FACE", ["demo-1", "demo-2"], 1),
    ("面霜", ["demo-3"], 1),
    ("护肤", ["demo-3"], 1),
    ("[A]", ["demo-1"], 1),
    (".*", [], 0),
    ("  CREAM  ", [], 0),
    ("missing", [], 0),
])
def test_literal_search_and_filtered_statistics(listings, query, expected_ids, expected_shops):
    original = listings.copy(deep=True)
    result = search_products(listings, query)
    assert result.listings["source_product_id"].tolist() == expected_ids
    assert result.matching_listings == len(expected_ids)
    assert result.distinct_shops == expected_shops
    assert result.price_distribution["number_of_listings"].sum() == result.listings["price"].notna().sum()
    pd.testing.assert_frame_equal(listings, original)
    if not result.listings.empty:
        result.listings.iloc[0, result.listings.columns.get_loc("product_name")] = "changed result only"
        pd.testing.assert_frame_equal(listings, original)


def test_empty_catalog_and_null_search_columns(listings):
    result = search_products(listings.iloc[:0], "anything")
    assert result.matching_listings == result.distinct_shops == 0
    assert result.price_distribution.empty
    assert list(result.listings.columns) == list(listings.columns)
    assert not has_synthetic_data(listings.iloc[:0])
    assert search_products(listings.iloc[-1:], "anything").matching_listings == 0
    unknown_price = search_products(listings.iloc[-1:])
    assert unknown_price.matching_listings == 1
    assert unknown_price.distinct_shops == 0
    assert unknown_price.price_distribution.empty


def test_warning_uses_full_catalog_even_when_search_has_no_matches(listings):
    assert has_synthetic_data(listings)
    assert search_products(listings, "missing").listings.empty
    assert has_synthetic_data(listings)
    # Missing provenance is not a synthetic label or proof of real data rights.
    assert not has_synthetic_data(pd.DataFrame({"data_origin": [None]}))


def test_missing_required_fields_are_reported(listings):
    with pytest.raises(ValueError, match="Missing required columns: price"):
        search_products(listings.drop(columns="price"))
    with pytest.raises(ValueError, match="Missing required column: data_origin"):
        has_synthetic_data(listings.drop(columns="data_origin"))


def test_core_modules_import_without_loading_streamlit():
    subprocess.run(
        [sys.executable, "-c", "import sys; import marketlens.repository; "
         "import marketlens.product_service; assert 'streamlit' not in sys.modules"],
        check=True, capture_output=True, text=True,
    )
