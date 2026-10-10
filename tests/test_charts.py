"""Synthetic regression cases for interval serialization and app empty states."""
import json
from pathlib import Path
import sqlite3

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from marketlens.charts import price_distribution
from marketlens.ingest import import_csv, init_db

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("prices, expected_counts", [
    ([], []), ([39.9], [1]), ([10.0, 10.0], [2]),
    ([28.0, 39.9, 55.0], [1, 1, 1]), ([None, 10.0], [1]),
])
def test_distribution_has_serializable_labels_and_preserves_counts(prices, expected_counts):
    series = pd.Series(prices, dtype="float64")
    result = price_distribution(series)
    assert list(result.columns) == ["price_range", "number_of_listings"]
    assert result["number_of_listings"].sum() == series.notna().sum()
    assert all(isinstance(label, str) for label in result["price_range"])
    assert result["number_of_listings"].tolist() == expected_counts


def test_ranges_keep_numeric_order_instead_of_alphabetical_order():
    result = price_distribution(pd.Series([200.0, 2.0, 100.0]))
    assert result["price_range"].tolist() == ["(1.802, 68.0]", "(68.0, 134.0]", "(134.0, 200.0]"]
    assert result["price_range"].cat.ordered
    assert result["price_range"].cat.categories.tolist() == result["price_range"].tolist()


def test_all_unknown_prices_are_empty():
    assert price_distribution(pd.Series([None, float("nan")], dtype="float64")).empty


@pytest.mark.parametrize("record_count", [0, 1, 3])
def test_app_chart_and_empty_database(tmp_path, monkeypatch, record_count):
    # Every test runs against a private temporary database, never the user's DB.
    monkeypatch.chdir(tmp_path)
    if record_count:
        rows = (ROOT / "examples/demo_synthetic.csv").read_text(encoding="utf-8").splitlines()
        fixture = tmp_path / "synthetic_demo.csv"
        fixture.write_text("\n".join(rows[:record_count + 1]) + "\n", encoding="utf-8")
        import_csv(fixture, tmp_path / "marketlens.db")
    else:
        with sqlite3.connect(tmp_path / "marketlens.db") as conn:
            init_db(conn)

    app = AppTest.from_file(str(ROOT / "app.py")).run()
    assert not app.exception
    charts = app.get("vega_lite_chart") or app.get("arrow_vega_lite_chart")
    if not record_count:
        assert not charts
        assert "no product snapshots" in app.info[0].value
        return

    assert len(charts) == 1
    spec = json.loads(charts[0].proto.spec)
    assert spec["encoding"]["x"]["field"] == "price_range"
    assert spec["encoding"]["x"]["type"] == "ordinal"
    expected = price_distribution(pd.Series([39.9, 55.0, 28.0][:record_count]))
    assert spec["encoding"]["x"]["sort"] == expected["price_range"].tolist()
    assert spec["encoding"]["y"]["field"] == "number_of_listings"
    assert any("SYNTHETIC" in warning.value for warning in app.warning)
    assert [metric.value for metric in app.metric] == [str(record_count), str(record_count)]

    app.text_input[0].set_value("奶油法式").run()
    assert not app.exception
    assert [metric.value for metric in app.metric] == ["1", "1"]
    assert app.dataframe[0].value["product_name"].tolist() == ["奶油法式穿戴甲"]
    assert any("SYNTHETIC" in warning.value for warning in app.warning)

    app.text_input[0].set_value("no_synthetic_product_matches_this").run()
    assert not app.exception
    assert not (app.get("vega_lite_chart") or app.get("arrow_vega_lite_chart"))
    assert app.info[0].value == "No matches."
    assert [metric.value for metric in app.metric] == ["0", "0"]
    assert any("SYNTHETIC" in warning.value for warning in app.warning)


def test_app_missing_database_shows_import_hint(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = AppTest.from_file(str(ROOT / "app.py")).run()
    assert not app.exception
    assert "No data yet" in app.info[0].value
    assert not (tmp_path / "marketlens.db").exists()
