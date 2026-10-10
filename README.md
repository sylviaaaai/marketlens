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

Previous chart-fix validation (2026-10-09): `.\.venv\Scripts\python.exe -m pytest -q` — **13 passed**. This validates the legacy demo, not completion of the consumer MVP.

## Phase 1A — 已实现的模块拆分（2026-10-10）

当前仍是 V0.0 数据与页面功能；这次只拆分职责，没有创建消费者版表、API 或 AI 功能。

| 文件 / 关键接口 | 职责、输入与输出 |
|---|---|
| `marketlens/repository.py` / `load_latest_listings(db_path)` | 输入已有 SQLite 文件路径，以只读连接返回每个上架条目的最新快照 DataFrame；保留来源、商品 ID、URL、观察时间等全部旧字段 |
| `marketlens/product_service.py` / `search_products(listings, query="")` | 输入商品表与搜索文字，返回 `ProductSearchResult`，包含筛选结果、商品条目数、店铺数和价格分布表；不访问数据库、不依赖 Streamlit |
| `marketlens/product_service.py` / `has_synthetic_data(listings)` | 输入完整商品表，返回是否含 `synthetic_demo`；搜索后无结果也仍保留全数据集的合成警告 |
| `marketlens/charts.py` / `price_distribution(prices)` | 现有函数，由服务复用；输入数值价格列，输出有序价格区间与条目数 |
| `app.py` | 接收搜索输入、调用数据访问和服务接口、显示表格/指标/图表及提示 |
| `marketlens/ingest.py` | 现有 CSV 校验、导入与建表入口，保持原有实现 |

`ProductSearchResult` 的字段含义：

- `listings`：筛选后的 DataFrame，保留输入列；返回副本，修改它不会改变输入表。
- `matching_listings`：筛选后的上架条目数，不是跨店铺合并后的独立产品数。
- `distinct_shops`：筛选后非空店铺名称的去重数量，沿用旧页面口径。
- `price_distribution`：DataFrame，`price_range` 是按价格顺序排列的文字类别，`number_of_listings` 是区间内已知价格的条目数。

搜索按商品名称或品类进行不区分大小写的文字子串匹配，不把 `.*`、`[` 等解释成正则表达式。空查询保留所有行，空格按原样匹配。空结果的两个计数均为 0，价格分布为空；未知价格不补零。服务要求 `product_name`、`category`、`shop_name`、`price` 列，来源判断要求 `data_origin` 列；缺列会抛出明确的 `ValueError`。

数据访问接口缺少数据库时抛出 `FileNotFoundError`，不会创建新文件；已有表无记录时返回保留列名的空表。表结构错误会抛出 `pandas.errors.DatabaseError`，不会静默建表。最新快照仍按旧 SQL 的 `observed_at` 文本降序、再按快照 ID 降序选择；本次没有改变时间排序语义。

脱离网页调用的 Python 示例：

```python
from marketlens.repository import load_latest_listings
from marketlens.product_service import has_synthetic_data, search_products

listings = load_latest_listings("marketlens.db")
is_demo = has_synthetic_data(listings)
result = search_products(listings, "穿戴甲")
print(is_demo, result.matching_listings, result.distinct_shops)
print(result.listings)
```

未来 FastAPI 或 AI 工具包装层可以调用这些 Python 接口。`ProductSearchResult` 目前含 DataFrame，不是直接的 JSON 响应；到接入 API 时再转换记录、空值及类别，并保留来源信息。React 通过后端 API 获取结果。AI 工具只暴露受控搜索参数，数据库路径由服务端配置，不开放任意 SQL。

当前开发环境与运行命令（在项目根目录执行）：

```powershell
# 已有环境，不需要重新安装依赖或重新导入现有数据库
& D:\PythonEnvs\marketlens\Scripts\python.exe -m streamlit run app.py

# 单独测试数据访问或搜索服务
& D:\PythonEnvs\marketlens\Scripts\python.exe -m pytest tests/test_repository.py -q
& D:\PythonEnvs\marketlens\Scripts\python.exe -m pytest tests/test_product_service.py -q

# 全部测试
& D:\PythonEnvs\marketlens\Scripts\python.exe -m pytest -q
```

实际验证：基线 **13 passed**；重构后 **31 passed in 1.27s**（Python 3.11.4、Streamlit 1.65.0、Pandas 2.3.3、pytest 9.1.1）。数据库测试和 AppTest 使用临时合成数据；测试覆盖搜索、统计、空结果、只读查询前后文件一致性、最新快照、来源保留、页面行为及核心模块不加载 Streamlit。现有 `marketlens.db` 的 SHA-256 在重构前后相同。浏览器视觉效果未人工验收。

## Scope guard

- V0.1: source-aware CSV ingestion; link brands/products/listings/social posts/reviews; deterministic pricing/aspect comparison and an evidence-based consumer Streamlit flow.
- V0.2: approved source adapters, LLM processing/evaluation.
- Future: multiple platforms, autonomous agents, prediction.

**Do not gather protected platform data without permission.** Do not commit private account credentials, cookies, personal data or any third-party text without rights.

## Legacy documents

Earlier merchant-focused notes live under `docs/legacy_v00/` for historical context only. They are superseded by the consumer-facing specification and should not guide new development.
