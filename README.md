# MarketLens Consumer Edition — Codex Handoff

**Status: Specification V1.0 + Legacy V0.0 Starter** (not yet a complete consumer MVP).

**Phase 1D Final Preflight：** 五种排序均成功返回，两篇有限滚动均取得20个唯一一级评论；“最新”的时间语义及严格去重停止条件仍有证据限制，目标30诊断失败。多Project内存验收及Run统计隔离修复完成。正式采集决策以[最终验收报告](docs/phase1d_xhs_baseline_collection/preflight_validation_report.md)为准，未启动50组。

**2026-10-14 / Phase 1D分阶段增强：** 正式Baseline使用独立库；显式`--start-baseline`仅执行50组搜索并暂停，详情须另行启动且受SQLite完成状态门槛保护。新增阶段检查点、边界暂停及Windows DPAPI加密临时参数缓存，支持同用户跨进程恢复。此次仅离线测试与空库初始化，原Pilot保留，未启动正式采集。见[正式操作说明](docs/phase1d_xhs_baseline_collection/formal_collection.md)。

**2026-10-14 / Phase 1D：粉底液基准采集 Pilot 已通过。** 4组单页搜索保留79条Hit、69篇唯一图文候选；5篇详情保存50条一级评论和29条二级回复，其余64篇未请求。首组500经同Run一次有限恢复成功，失败证据保留。完整50组仅完成离线验证，未正式采集或冻结Dataset；20+10追加控制及跨进程临时访问参数仍有限制。见 [Phase 1D 使用说明](docs/phase1d_xhs_baseline_collection/README.md) 和 [Pilot 报告](docs/phase1d_xhs_baseline_collection/pilot_report.md)。完整回归69 passed。

**2026-10-14 / Phase 1C：真实小红书图文入库 Smoke Test 已通过。** 独立研究库保存20条SearchHit、3个详情快照、13条图片元数据和48条评论观察（一级30、二级18）；旧商品库与页面不变。Dataset尚未构建。运行及数据边界见 [Phase 1C README](docs/phase1c_xhs_ingestion_smoke/README.md)，实测证据见 [Experiment 01](docs/phase1c_xhs_ingestion_smoke/experiment01_report.md)。本地响应和实验库位于Git忽略的local_data目录，不是仓库演示数据。

An evidence-grounded shopping research assistant combining permitted social discussion records, marketplace product information and consumer review evidence. The first case study is moisturizer, but the product schema is intended to be extensible.

## Start here

### 当前实验目录导航

日常先看 [Phase 1C 实验结果](docs/phase1c_xhs_ingestion_smoke/experiment01_report.md)；要运行或检查数据，看 [Phase 1C 使用说明](docs/phase1c_xhs_ingestion_smoke/README.md)。数据库设计看 [Phase 1B 索引](docs/phase1b_collection_dataset_schema/README.md)。

```text
marketlens/                         项目根目录
├─ marketlens/                      Python代码
│  ├─ adapters/                     小红书服务调用、错误处理、凭据脱敏
│  ├─ research/                     研究实验代码
│  │  ├─ database.py                独立实验库连接、建表、事务
│  │  ├─ normalizers.py             ID、互动量、时间字段解析
│  │  ├─ collection_pipeline.py     搜索→详情/评论→入库及审计
│  │  └─ dataset_builder.py         占位文件，尚未实现Dataset
│  ├─ repository.py                既有商品数据库查询
│  ├─ product_service.py           既有商品搜索与统计
│  └─ ingest.py / charts.py        既有CSV导入、图表处理
├─ scripts/
│  └─ phase1c_xhs_smoke.py          实验命令入口
├─ tests/
│  ├─ research/test_smoke.py        研究采集离线测试，使用合成响应
│  └─ test_*.py                    既有商品功能测试
├─ docs/
│  ├─ phase1c_xhs_ingestion_smoke/  当前实验：README说明 + experiment01_report结果
│  ├─ phase1b_collection_dataset_schema/
│  │  ├─ schema/                   当前v0.2.1 SQL与ERD
│  │  ├─ tests/                    Schema回归脚本
│  │  ├─ review_inputs/            旧版输入与原始材料，回归依赖
│  │  └─ reports/                  审核与修复依据，部分被测试读取
│  ├─ MarketLens_Phase1B_Design_Log_v0.7.md    最新累计设计日志
│  ├─ MarketLens_Phase1B_Data_Collection_Spec_v0.1.md  原始采集需求
│  ├─ PRODUCT_SPEC / TECHNICAL_ARCHITECTURE / IMPLEMENTATION_PLAN 等.md
│  │                               产品、架构、计划、来源登记与工作记录
│  └─ legacy_v00/                  早期路线与数据可行性文档，平时可跳过
├─ local_data/phase1c_smoke/        本地真实实验数据，不提交Git
│  ├─ research_smoke.db            当前实验数据库
│  ├─ raw/                        4份脱敏响应，与数据库Artifact关联
│  └─ logs/                       4份证据：请求事件、实验摘要、验收、原文件指纹
├─ examples/                      合成演示CSV，不是真实实验结果
├─ marketlens.db                  既有商品数据库
├─ app.py                         既有商品页面
└─ .venv/                         本地Python环境，非实验产物
```

2026-10-14整理：已核对v0.2～v0.6设计日志内容均被v0.7完整覆盖（仅版本行不同），删除5份旧副本及可再生成缓存。当前实验数据和回归依赖保留。后续同一实验优先更新现有README/报告；仅在执行新的真实实验时追加必要证据，不因文字调整复制新版本文档。

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


## Phase 1D comments and images final closeout (2026-10-14)

Formal Baseline now sends the accepted finite DOM-20 comment configuration through the existing Adapter; Pilot and Phase1C defaults remain unchanged. Focused offline regression: 15 passed, SQLite in memory only. Two Pilot posts: 10 body images and 2 covers downloaded and decoded successfully (813,820 bytes total). Image downloading is NOT connected to formal collection. Four existing database hashes unchanged; formal Run count remains zero. No formal searches, new MCP requests, new SQLite files, commit or push. See [closeout report](docs/phase1d_xhs_baseline_collection/comments_images_final_report.md). Awaiting user review before any further integration.


## Phase 1D unified storage integration (2026-10-14)

Formal details now commit text/comments before downloading all returned body images through the existing safe transport. Content-addressed files and recoverable Run manifests retain independent Media associations. The existing CLI adds formal-only --resume-media and Run-scoped --export-run (Project ownership required); SQLite remains authoritative and no Dataset is frozen. Formal default stops after search. Full pytest collection in the no-disk audit profile: 106 passed, 40 skipped because those legacy tests require new file SQLite, 0 failed. Four existing database hashes and DDL unchanged; formal database remains at 0 Run. No real MCP/media requests, formal collection, new SQLite files, commit or push. See [storage integration report](docs/phase1d_xhs_baseline_collection/storage_integration_report.md) and [storage layout](docs/phase1d_xhs_baseline_collection/storage_layout.md). Awaiting user review.
