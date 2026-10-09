# Copy this prompt into Codex (first turn)

请作为 MarketLens Consumer Edition 项目的开发助手接手当前仓库。

**先不要改任何代码，也不要开始爬取网站。**

请按以下顺序阅读：`AGENTS.md`、`docs/PRODUCT_SPEC.md`、`docs/TECHNICAL_ARCHITECTURE.md`、`docs/IMPLEMENTATION_PLAN.md`、`docs/DATA_SOURCE_REGISTER.md`，然后检查仓库中真实存在的代码，特别是 `marketlens/ingest.py`、`app.py`、`tests/test_ingest.py`、`examples/demo_synthetic.csv`。

请返回以下内容：
1. 一段你对项目目标的理解：这是面向普通消费者、整合社交讨论与电商价格/评价、带来源证据的 AI 购物研究助手，而不是商家选品工具。
2. 当前已有功能与规范要求之间的 Gap Analysis（哪些确实已经实现，哪些还只是文档计划）。
3. 先完成 Gate 0、Gate 1 的细化实现计划：建议修改/新增哪些文件、数据库表、数据导入模板、测试；说明如何保留旧 Starter 的运行能力。
4. 至少列出 3 个你看到的风险与待确认问题，尤其是数据许可、跨来源商品匹配、价格规格可比性。
5. 给出第一轮最小可审查改动（最好不超过 1 个模块 + 对应测试）。

**完成以上分析后停下来等待我确认。** 不能自行开始 P1/P2，不要调用任何需登录或付费的外部数据服务；不能将合成数据伪装成真实商品数据。

# Follow-up prompt (after approving the plan)

请按已批准的计划，只实现 Gate 0 和 Gate 1 的第一小步：补足最小消费品数据结构和一条能运行的 CSV 导入路径，保留旧入口，使用明确标记 `synthetic_demo` 的测试样本；为新增功能编写 pytest，运行并给出真实结果。更新 README 与 WORK_LOG，然后停下来等待 Review。不要提前做小红书/淘宝爬虫、LLM 推荐、Agent 或 UI 大改。
