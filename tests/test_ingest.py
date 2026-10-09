import csv
import sqlite3
from marketlens.ingest import import_csv, parse_displayed_sales


def test_sales_parser_preserves_uncertainty():
    assert parse_displayed_sales("已售386") == (386, True)
    assert parse_displayed_sales("300+") == (300, False)
    assert parse_displayed_sales("1.2万+") == (12000, False)
    assert parse_displayed_sales("") == (None, False)


def test_csv_ingestion_idempotent(tmp_path):
    headers = ["source", "source_product_id", "product_name", "shop_name", "price", "displayed_sales", "observed_at", "data_origin", "category", "product_url"]
    item = {"source":"merchant", "source_product_id":"p1", "product_name":"测试穿戴甲", "shop_name":"测试店铺", "price":"39.9", "displayed_sales":"300+", "observed_at":"2026-10-08T18:00:00+08:00", "data_origin":"synthetic_demo", "category":"穿戴甲", "product_url":""}
    csvfile = tmp_path / "records.csv"
    with csvfile.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerow(item)
    db = tmp_path / "test.db"
    assert import_csv(csvfile, db) == (1, 1)
    assert import_csv(csvfile, db) == (0, 0)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 1


def test_rejects_unlabeled_source(tmp_path):
    csvfile = tmp_path / "bad.csv"
    csvfile.write_text("source,source_product_id,product_name,shop_name,price,displayed_sales,observed_at,data_origin\nmerchant,p1,x,y,10,1,2026-10-08T18:00:00+08:00,unknown\n", encoding="utf-8")
    import pytest
    with pytest.raises(ValueError, match="invalid data_origin"):
        import_csv(csvfile, tmp_path / "bad.db")
