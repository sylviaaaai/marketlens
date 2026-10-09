# MarketLens Consumer Edition — Codex Handoff

**Status: Specification V1.0 + Legacy V0.0 Starter** (not yet a complete consumer MVP).

An evidence-grounded shopping research assistant combining permitted social discussion records, marketplace product information and consumer review evidence. The first case study is moisturizer, but the product schema is intended to be extensible.

## Start here

- **`CODEX_START_HERE.md`** — prompt to send to Codex. Ask it to inspect and plan first, **not** implement everything.
- **`AGENTS.md`** — persistent rules for Codex within this repo.
- `docs/PRODUCT_SPEC.md` — product scope, use case, acceptance criteria.
- `docs/TECHNICAL_ARCHITECTURE.md` — components, database and CSV interfaces.
- `docs/IMPLEMENTATION_PLAN.md` — gate-by-gate development plan.
- `docs/DATA_SOURCE_REGISTER.md` — source permission status (currently unverified for XHS/Taobao/JD).
- `docs/WORK_LOG.md`, `docs/BACKLOG.md`, `docs/DECISIONS.md` — execution, scope, ADRs.

## Legacy starter already present (V0.0)

Included starter modules implement only a limited local CSV → SQLite → searchable Streamlit demo with **synthetic example data**. They are intentionally preserved for Codex to migrate incrementally.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m marketlens.ingest examples/demo_synthetic.csv
python -m pytest -q
python -m streamlit run app.py
```

On macOS/Linux use `python3 -m venv .venv` and `source .venv/bin/activate`.

This legacy ingestion takes: `source,source_product_id,product_name,shop_name,price,displayed_sales,observed_at,data_origin,category,product_url`. The new normalized domain and multi-input adapters in the architecture docs are **not yet implemented**. Do not present old synthetic demo metrics as real evidence.

Price distribution uses ordered text labels for price intervals, avoiding Altair interval serialization errors. Single-record and equal-price samples are supported; empty results skip the chart, and unknown prices are excluded rather than replaced with zero. Regression tests cover chart generation, interval ordering, and empty search results.

Latest local validation (2026-10-09): `.\.venv\Scripts\python.exe -m pytest -q` — **13 passed**, using Streamlit 1.65.0, Pandas 2.3.3, and Altair 6.3.0. This validates the legacy demo, not completion of the consumer MVP.

## Scope guard

- V0.1: source-aware CSV ingestion; link brands/products/listings/social posts/reviews; deterministic pricing/aspect comparison and an evidence-based consumer Streamlit flow.
- V0.2: approved source adapters, LLM processing/evaluation.
- Future: multiple platforms, autonomous agents, prediction.

**Do not gather protected platform data without permission.** Do not commit private account credentials, cookies, personal data or any third-party text without rights.

## Legacy documents

Earlier merchant-focused notes live under `docs/legacy_v00/` for historical context only. They are superseded by the consumer-facing specification and should not guide new development.
