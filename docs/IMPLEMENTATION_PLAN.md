# MarketLens — Codex Implementation Plan & Acceptance Gates V1.0

> 时间估计是目标，不是承诺；P0 失败时不自动推进 P1。项目可持续迭代，用户可以在任意可靠版本将“已完成部分”用于申请材料。

## 0. 从旧 starter 开始（已随包提供）

旧 starter 具备：`marketlens.ingest`、CSV→SQLite 商品快照、简单搜索 Streamlit 页面、合成演示样本、`tests/test_ingest.py`。**它不是完整的消费者 AI 导购系统**。Codex 先检查旧仓库的运行状态与测试，再渐进迁移；不要删掉已工作的部分。

### Gate 0 — Baseline & Repo Hygiene（第一步，约 1–2 小时）

- 阅读 `AGENTS.md`, `docs/PRODUCT_SPEC.md`, `docs/TECHNICAL_ARCHITECTURE.md`, `docs/IMPLEMENTATION_PLAN.md`。
- 运行当前已有测试，记录运行命令/结果；检查 starter 文件结构与现有 schema；列出差距。
- 确认 `.gitignore` 忽略 `.env`, `.venv`, `.sqlite3`, cookies, data/private/, local datasets 与缓存。
- 完成 `docs/WORK_LOG.md` 基线记录；明确计划做什么以及不做什么。
- 通过条件：旧导入与搜索功能没有被破坏；测试仍可复现。

### Gate 1 — Core Domain + 4 CSV Contracts（Day 1–2）

目标：**任何平台能否抓取，均不影响本地分析核心**。

- 将核心实体与来源记录建成可迁移 SQLite schema；迁移脚本不可对已有数据库进行无提示破坏。
- 做 Pydantic/明确校验函数：产品、listing、price_snapshot、social_post、mention、purchase_review、aspect。
- 制作空白 CSV 模板及 6–8 款**显式 synthetic_demo** 的相互关联样本，用来验证完整流程；不要生成伪装成现实口碑的评论。
- 导入器提供有效行计数/拒绝行计数及原因，符合幂等键；字段缺失时不静默用 0 替代。
- Gate 1：`pytest` 通过；数据能连通 `social_posts → mentions → products → listings → price_snapshots/purchase_reviews`。

### Gate 2 — Product Identity & Deterministic Analytics（Day 3–4）

- 先精确匹配已有 `product_key` / 人工确认关系，再实现谨慎的品牌+版本+单位规则辅助发现。
- 遇到相同品牌不同版本或不同容量必须输出 `candidate` 或不同产品，不允许误合并。
- 计算预算符合性、价格可比性、每 100mL 标准化单价、社交提及去重数、电商评价主题计数与证据数。
- 推出 `shopping_flow.research(query, budget, priorities)`，返回定义良好的结构化结果对象，不依赖 Streamlit 或 LLM。
- Gate 2：至少 8 类边缘情况（重复、缺失、未知/跨币种、套装、版本不明、无评论、无匹配、授权状态不明）有自动测试；可用 CLI 显示对比结果。

### Gate 3 — First Visual MVP（Day 5–6）

- Streamlit 4 个逻辑页面/标签：Finder、Results、Compare、About Data / Evidence。
- 从 SQLite 调用购物研究服务，不在界面内手工复制业务逻辑。
- 用户可以搜索、设预算与偏好、查看 2 个产品的并排比较、点击数据来源链接、看到缺失情况。
- 默认演示模式始终有清晰“合成示例，不代表实际商品评价”标签；无真实数据不会默默使用合成数据得出事实性推荐。
- Gate 3：全新环境按 README 完整复现，从导入数据到界面比较都成功；截图用于说明功能，标注 Demo 数据。

### Gate 4 — Evidence-Grounded Output & Evaluation（Day 7）

- 先生成模板式中文解释：每个主张对应 source/evidence ID（没有证据不能输出确定性主张）。
- 做 baseline：仅预算与单位价格排序；对比加入公开评价主题/覆盖信息后的推荐逻辑。不是要求后者必胜。
- 编写可重复的 5 条合成用户查询测试（预算内/外、清爽诉求、价格优先、负反馈优先、无可比样本），人工核对证据链。
- Gate 4：所有 CI/本地测试通过，`README.md` 列出已完成/未完成/数据访问局限；形成可展示 V0.1。

### Gate 5 — Real Data Feasibility（与 Day 1–7 并行，必须单独验收）

- 记录小红书笔记/评论、淘宝/天猫商品、其他可选择数据源的访问条款、授权可能性、允许字段、费用、频率、导出和署名限制。
- 来源未被许可时标记 `pending` 或 `rejected`，不得编写规避登录、验证码或封禁的采集器。
- 真实数据数量不足时仍保留合成 Demo 作为**软件功能展示**，但报告须说明没有实证结论；申请材料不可写成完成了真实市场分析。
- 最理想通过条件：至少一个具备明确合法使用范围的商品数据来源 + 一个许可明确的社交/评论数据来源，并完成部分 product-level 实例匹配。若未达到，保留接口、继续寻找来源，不假装达成。

## P1 — V0.2 开发任务（随后迭代）

- A：合规可运行数据源适配器，手动/定期刷新，观测时间戳和错误日志。
- B：商品匹配算法可替换实现：rule-based baseline、TF-IDF/embedding；人工标注与 precision/recall。
- C：LLM 评论主题提取 + 结构化报告：证据约束、类型验证、成本记录；与无 LLM 基线比较。
- D：数据趋势图、分析导出、用户反馈记录、可选后台 API。
- E：用户任务评估：对比“最便宜优先”与“基于用户偏好+评价证据”的方案，记录决策一致性、证据支持率与用户满意度，说明小样本偏差。

## 整体验收与报告

每个 gate 最少有 4 项产出：`代码`、`自动测试`、`README/示例命令`、`WORK_LOG`。不要为了完成任务数量而假设没有执行过的测试通过。申请材料中只能使用当前真实完成状态。

## 第一批 Issue 建议

- `#01 Repo baseline and data/source audit`
- `#02 Create normalized SQLite schema + migration`
- `#03 Import source-aware CSV contracts`
- `#04 Link social mentions to product listings`
- `#05 Deterministic pricing and budget engine`
- `#06 Evidence-backed product comparison`
- `#07 Streamlit consumer UI`
- `#08 Test suite + README/demo recording`
- `#09 Investigate licensed social data access`（独立跟踪，不阻塞 demo）

## 项目管理原则

1. 单次 Codex 任务聚焦一个 Gate，提交 diff 供复核；不一次实现所有 P0/P1/P2。
2. 不擅自调用付费 API、不添加大规模依赖、不执行有副作用的外网操作。
3. 优先保证垂直闭环与可复现，保留中间结果与来源。
4. 发现设计冲突时先更新 ADR（Architecture Decision Record / `docs/DECISIONS.md`），说明取舍。
