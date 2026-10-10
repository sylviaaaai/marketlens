# MarketLens Work Log

## 2026-10-09 — Product/architecture handoff

- Scope set to consumer-oriented shopping research rather than merchant product selection.
- First test vertical: moisturizer, with generalizable product architecture.
- Four-stage user journey defined: Discover → Match → Analyze → Recommend.
- MVP and follow-up features separated; real source permissions remain unverified.
- Technical specifications and staged Codex instructions written.
- **No new consumer MVP modules are claimed to be implemented at this stage.**

### Future entry template

Date / Gate / What changed / How to run / Tests actually executed / Data provenance / Limitations / Next actions

## 2026-10-09 — Legacy price distribution bug fix

- Scope: only the reported chart error; no consumer MVP gate advancement.
- Reproduced the original `pd.cut` / `groupby` / `st.bar_chart` expression with synthetic prices: Altair raised `SchemaValidationError` because categorical interval objects entered the chart specification.
- Added `marketlens/charts.py` to count known prices and convert intervals into ordered text categories; updated only the price-chart block and its import in `app.py`.
- Empty data/unknown-only prices yield no chart data. Single-record and equal-price inputs retain their counts. Existing empty-database and no-match messages are preserved.
- Added `tests/test_charts.py`: count and label tests plus Streamlit AppTest runs against temporary synthetic databases with 0, 1, and 3 records and no-match searches. The user's database is not written by these tests.
- Installed existing `requirements.txt` dependencies into the project `.venv`; dependency requirements were not changed.
- Validation: `.\.venv\Scripts\python.exe -m pytest -q` — **13 passed in 0.86s**, including the 3 existing ingestion tests. Environment: Python 3.11.4, Streamlit 1.65.0, Pandas 2.3.3, Altair 6.3.0.
- Run: refresh the existing page, or use `.\.venv\Scripts\python.exe -m streamlit run app.py`.
- Limits: headless AppTest verifies chart generation and axis specification; browser appearance was not visually inspected. Other allowed dependency versions were not run. Consumer schema, matching, reviews, and recommendation remain planned.
- No new external data was collected. Stop after this fix for user review.

## 2026-10-10 — Phase 1A: 模块化重构

- 用户限定任务：从旧 `app.py` 拆分查询、搜索与统计；不推进消费者版 schema、FastAPI 或 AI 功能。
- 基线：Git 工作区干净；使用 `D:\PythonEnvs\marketlens\Scripts\python.exe -m pytest -q`，结果 **13 passed in 0.88s**。
- 新增 `marketlens/repository.py`：`load_latest_listings(db_path)` 以 SQLite URI `mode=ro` 读取现有表，显式关闭连接；保留旧 SQL 的最新快照选择及返回列；缺少文件不创建数据库。
- 新增 `marketlens/product_service.py`：`search_products()` 返回带类型注解的 `ProductSearchResult`，提供结果表、条目数、店铺数与价格分布；`has_synthetic_data()` 判断完整数据集的合成标记。服务不访问 SQLite，也不 import Streamlit，直接复用现有 `charts.price_distribution()`。
- 简化 `app.py`：保留输入和展示，调用两个独立模块；保留合成警告在筛选前判断、原搜索口径、指标、图表及空状态。
- 新增 `tests/test_repository.py`、`tests/test_product_service.py`，扩充 `tests/test_charts.py` 的页面指标、有效搜索、无结果警告及缺少数据库回归检查。测试只在临时数据库写入合成样本。
- 首轮新增测试有两项测试自身问题：空格样例实际包含于测试名称；文件重命名断言受到准备样本时其他连接的影响。修正空格样例，移除不隔离的重命名断言，保留数据库字节一致性检查。
- 最终全部测试：`& D:\PythonEnvs\marketlens\Scripts\python.exe -m pytest -q` → **31 passed in 1.27s**。环境：Python 3.11.4、Streamlit 1.65.0、Pandas 2.3.3、pytest 9.1.1。
- 数据保护：现有 `marketlens.db` 重构前后 SHA-256 都为 `A8EBCE4B1F81EA0B16C3659144660CA3359E1412052DE35AB4A602B96F7A8080`。没有改变表结构、原始数据、`ingest.py` 或 `charts.py`。
- README 新增当前接口含义、独立调用示例、指定虚拟环境的启动/测试命令及后续 API 包装边界。
- 运行：`& D:\PythonEnvs\marketlens\Scripts\python.exe -m streamlit run app.py`。
- 限制：保留旧版时间文本排序与店铺名称去重口径；结果对象包含 DataFrame，未来 API 层仍需做 JSON 转换；本轮使用无浏览器 AppTest，没有人工视觉验收。消费者数据结构、评论分析与推荐仍未实现。
- 无新增依赖，无外部数据访问，无 Commit/Push；本阶段完成后等待用户检查。当前无阻塞。
