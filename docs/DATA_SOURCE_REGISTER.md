# Data Source Register — MarketLens

> 此表仅列出**候选来源与访问状态**；无任何项目已经取得许可的声明。

| Source | Expected records | Current status | Access/use precondition | MVP fallback |
|---|---|---|---|---|
| Xiaohongshu | 社交笔记、产品提及、消费者讨论 | **unverified / no collector enabled** | 核实平台规则、用途许可、可能的授权数据服务； | 合法持有的短摘要/标签手工导入；无权限则不可作实证结论 |
| Taobao / Tmall | 商品 listing、品牌、规格、显示价格、购买评价 | **unverified / no collector enabled** | 开放平台/联盟 API 接入审批、字段范围、评论数据授权等 | 许可 CSV 或其他数据源；不声称实时淘宝价格 |
| JD | 商品 listing、显示价格、部分产品信息 | **unverified / no collector enabled** | 开放平台/联盟接口资格与允许用途 | 后续扩展 |
| Approved CSV | 授权出口数据、研究者获得使用许可的记录 | **supported in legacy form only** | 记录所有者授权和使用范围，移除 PII | 作为消费者 MVP 数据导入路线 |
| `examples/demo_synthetic.csv` | 旧 starter 的人工合成商品价格演示 | **available — synthetic only** | 仅用于软件测试；不得代表市场观测 | 本地联调 |
| `examples/demo_consumer/*.csv` | 计划创建的相互关联商品/笔记/评价合成例子 | **not yet implemented** | 需显式标记 synthetic | 端到端功能示范 |

数据字段可用性、第三方费用、访问时间、许可编号和保存期限都需要用户确认后更新。本地开发数据要分成 `examples/`（明确合成、可公开）与 `data/private/`（权限数据、禁止提交）。

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
