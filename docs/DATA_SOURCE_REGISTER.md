# Data Source Register — MarketLens

> 更新日期：2026-10-21。此表区分**技术实现状态**与**访问及再分发权限状态**；功能已实现不表示已获得平台授权。

| Source | Expected records | Current status | Access/use precondition | MVP fallback |
|---|---|---|---|---|
| Xiaohongshu | 社交笔记、产品提及、消费者讨论 | **Collection implemented; permissions unconfirmed.** 本地 Run 1 已封口，Dataset v1 已正式冻结并验证。 | 平台授权、采集及研究使用范围、保存期限和源内容再分发权限尚未得到确认；本仓库不构成获准声明。第三方工具的软件许可证不授予平台访问或内容再分发权限。 | 仓库仅提供代码、合成测试和聚合报告；真实数据库、帖子、评论、图片及认证材料保留在本地，不随仓库发布。后续采集或数据共享须另行核实适用条件。 |
| Taobao / Tmall | 商品 listing、品牌、规格、显示价格、购买评价 | **unverified / no collector enabled** | 开放平台/联盟 API 接入审批、字段范围、评论数据授权等 | 许可 CSV 或其他数据源；不声称实时淘宝价格 |
| JD | 商品 listing、显示价格、部分产品信息 | **unverified / no collector enabled** | 开放平台/联盟接口资格与允许用途 | 后续扩展 |
| Approved CSV | 授权出口数据、研究者获得使用许可的记录 | **supported in legacy form only** | 记录所有者授权和使用范围，移除 PII | 作为消费者 MVP 数据导入路线 |
| `examples/demo_synthetic.csv` | 旧 starter 的人工合成商品价格演示 | **available — synthetic only** | 仅用于软件测试；不得代表市场观测 | 本地联调 |
| `examples/demo_consumer/*.csv` | 计划创建的相互关联商品/笔记/评价合成例子 | **not yet implemented** | 需显式标记 synthetic | 端到端功能示范 |

数据字段可用性、第三方费用、访问时间、许可编号和保存期限应按实际证据更新。当前研究数据、Manifest、人工审核证据和 DPAPI 访问参数缓存保存在 Git 忽略的 `local_data/`；`data/private/` 也受忽略规则保护。`examples/` 仅用于明确标记的合成演示数据。

## 数据来源记录模板

- Source name / domain:
- Owner / permission evidence:
- Access allowed for: collection / storage / transformation / display / redistribution?
- Auth method and fees:
- Fields exposed / field reliability:
- Rate limits and refresh policy:
- Attribution and retention requirements:
- Reviewed date:
- Decision: `approved` / `pending` / `rejected`:
