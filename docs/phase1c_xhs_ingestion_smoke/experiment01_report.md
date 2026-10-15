# Experiment 01 — 真实小红书图文入库 Smoke Test

实验日期：2026-10-14（Asia/Shanghai）。状态：**SUCCESS，第一批真实数据已进入独立 SQLite**。

## 1. Executive Summary

按任务范围完成一次 `护手霜 × 综合 × 图文` 搜索和三次详情请求。保存全部20条搜索命中、20个稳定Post身份、3个图文Snapshot、13条图片元数据、48个Comment身份和48条Observation（一级30、二级18）。4个实际采集Attempt均成功；搜索没有重试。默认评论加载不是全量采集。

数据库为项目内 `local_data/phase1c_smoke/research_smoke.db`，Run ID为1。18表/165列来自原v0.2.1 candidate，外键检查为空，integrity_check为ok，4个脱敏响应文件的实际SHA-256/长度全部匹配。全部55项pytest通过（原有31项 + 新增24项）。Dataset三表仍为空。

## 2. 读取与文件变更

已读取/核对：

- `AGENTS.md`，`docs/PRODUCT_SPEC.md`、`docs/TECHNICAL_ARCHITECTURE.md`、`docs/IMPLEMENTATION_PLAN.md`、`docs/DATA_SOURCE_REGISTER.md`，README和WORK_LOG。
- `marketlens/repository.py`、现有模块/测试目录、requirements与Git状态。既有repository只读访问商品库，不能承担不同Schema的研究写入；因此独立新增research模块，不重复改造商品访问层。
- `docs/phase1b_collection_dataset_schema/schema/marketlens_phase1b_schema_v0_2_1_candidate.sql`及对应DBML；`reports/task1b_sqlite_v021_fix_report.md`；回归脚本入口。
- 兄弟目录 `data-source-lab/xiaohongshu-mcp/` 的接口数据字典、只读搜索探测脚本与docker-compose配置；运行时只读已有环境文件获取鉴权，未展示其内容。

新增：

- `marketlens/adapters/__init__.py`、`xiaohongshu_mcp.py`。
- `marketlens/research/__init__.py`、`database.py`、`normalizers.py`、`collection_pipeline.py`、`dataset_builder.py`（占位）。
- `scripts/phase1c_xhs_smoke.py`、`tests/research/test_smoke.py`。
- 本目录README与本报告。
- 仅本地的实验数据库、4个脱敏响应文件、事件/实验摘要/验收/保护文件哈希日志。

修改：`.gitignore`补充local_data、环境变量文件、Cookie、密钥与SQLite侧文件排除；根README和WORK_LOG追加本阶段状态。

开始时`AGENTS.md`、`marketlens/ingest.py`已有用户修改，Phase1B文件及多份设计文档已有未跟踪内容，全部保留。对旧库、旧业务模块、AGENTS和Phase1B共22个文件建立前后SHA-256对比，变化数0。未Commit/Push。

## 3. MCP版本、接口及服务状态

只读Docker检查确认：`marketlens-xhs`运行，镜像标记`xpzouying/xiaohongshu-mcp:v2.5.5`，仅映射`127.0.0.1:18060`。`GET /health`为HTTP200，服务自报版本`v2.5.5`；`GET /api/v1/login/status`为HTTP200且已登录。

本地指定目录实际包含可执行程序、配置、字典和实验文件，**未找到Go源码checkout**，没有凭空重建源码。接口核对使用已存数据字典、已暴露工具定义和上游固定v2.5.5源码：[路由](https://github.com/xpzouying/xiaohongshu-mcp/blob/v2.5.5/routes.go)、[HTTP处理器](https://github.com/xpzouying/xiaohongshu-mcp/blob/v2.5.5/handlers_api.go)、[请求结构](https://github.com/xpzouying/xiaohongshu-mcp/blob/v2.5.5/types.go)、[搜索筛选](https://github.com/xpzouying/xiaohongshu-mcp/blob/v2.5.5/xiaohongshu/search.go)、[字段类型](https://github.com/xpzouying/xiaohongshu-mcp/blob/v2.5.5/xiaohongshu/types.go)、[API文档](https://github.com/xpzouying/xiaohongshu-mcp/blob/v2.5.5/docs/API.md)。没有验证容器二进制与上游源码逐字节对应，不把版本标签当成构建证明。

Python Adapter使用该MCP服务的HTTP API facade，**没有走JSON-RPC tools/call**。搜索为`POST /api/v1/feeds/search`，详情为`POST /api/v1/feeds/detail`。只读使用已有凭据；无Docker配置、登录状态、Cookie或第三方代码修改。

初次沙箱内Docker查询因本地管道权限失败；经只读提权检查正常完成。这不是MCP服务故障；后续真实HTTP请求正常。

## 4. 实际搜索与详情参数

```json
{"keyword":"护手霜","filters":{"sort_by":"综合","note_type":"图文"}}
```

仅执行1次搜索，保存20/20条可识别结果；所有卡片类型为normal，视频0、未知0、无稳定ID0。20个唯一候选均有即时详情访问参数，仅对排序最前的3个唯一normal候选各请求1次。详情请求传入同一卡片的feed_id及内存中的访问参数，`load_all_comments=false`。访问参数未写入数据库或文件，报告也不包含平台ID或内容。

## 5. 实际开始/结束时间与耗时

下表均为UTC（北京时间加8小时），固定毫秒精度。duration_ms用单调时钟测量，可能与墙钟差值略有差异。

| 步骤 | 开始UTC | 结束UTC | 耗时ms | HTTP |
|---|---|---|---:|---:|
| 健康检查 | 2026-10-14T01:56:18.215Z | 2026-10-14T01:56:18.221Z | 0 | 200 |
| 登录状态检查 | 2026-10-14T01:56:18.221Z | 2026-10-14T01:56:23.125Z | 4906 | 200 |
| 搜索 / Attempt 1 | 2026-10-14T01:56:23.135Z | 2026-10-14T01:56:32.948Z | 9813 | 200 |
| 详情与评论 / Attempt 2 | 2026-10-14T01:56:32.972Z | 2026-10-14T01:56:39.566Z | 6593 | 200 |
| 详情与评论 / Attempt 3 | 2026-10-14T01:56:39.583Z | 2026-10-14T01:56:45.603Z | 6016 | 200 |
| 详情与评论 / Attempt 4 | 2026-10-14T01:56:45.623Z | 2026-10-14T01:56:52.162Z | 6531 | 200 |

搜索9.813秒，三次详情/评论合计19.140秒。评论与详情联合返回，共用同一真实Attempt，不能拆出独立评论请求耗时。采集阶段从首次搜索开始到最后详情结束约29.027秒。

## 6. 实际获取数量

| Attempt | 搜索返回 | Snapshot | 图片元数据 | 一级评论 | 二级回复 | hasMore |
|---|---:|---:|---:|---:|---:|---|
| 1 | 20 | — | — | — | — | — |
| 2 | — | 1 | 1 | 10 | 5 | true |
| 3 | — | 1 | 8 | 10 | 10 | true |
| 4 | — | 1 | 4 | 10 | 3 | true |
| 合计 | 20 | 3 | 13 | 30 | 18 | 非全量 |

默认模式未启用滚动或主动展开更多回复；未宣称精确限额。三个响应都提示hasMore=true。已返回的48条评论全部保留，不额外裁切、不用平台展示的总评论量充当已采集数。未下载图片，13条指元数据记录。

## 7. SQLite逐表行数

本次开始前为空库，下列既是本次写入数也是验收时总数。

| 表 | 行数 |
|---|---:|
| research_projects | 1 |
| collection_plans | 1 |
| plan_keywords | 1 |
| plan_sort_methods | 1 |
| collection_runs | 1 |
| search_requests | 1 |
| collection_tasks | 4 |
| collection_attempts | 4 |
| raw_artifacts | 4 |
| posts | 20 |
| search_hits | 20 |
| post_snapshots | 3 |
| post_media | 13 |
| comments | 48 |
| comment_observations | 48 |
| dataset_versions | 0 |
| dataset_posts | 0 |
| dataset_comments | 0 |

实际SQL核验：成功Attempt=4，失败=0；稳定Post重复键组=0；Hit与SearchRequest/Attempt不匹配=0；二级Observation父关系不匹配=0；非normal快照=0。48条评论均与对应Snapshot共享实际Attempt。另17个候选未创建详情任务，保留搜索来源，未标记为失败。

## 8. 数据质量与证据保存

- 空标题1/3，正文缺失0/3；空标题仍保存快照。
- likes/comments/favorites数值不可用均0/3；shares不可用3/3，其实际raw为空字符串，保留raw，value=NULL、precision=unparseable，没有写0。
- likes精度：exact_display 2、approximate_lower_bound 1；comments和favorites各为exact_display 2、approximate 1。没有把展示量误说成精确平台计数。
- 不可确认的发布时间0/3，评论时间0/48；无ID评论0/48；匿名父子关系已通过合成测试，本批没有匿名样本。
- 图片主URL缺失0/13；返回评论容器缺失0/3，非法评论项0。
- RawArtifact各指向一个本地JSON文件；4/4长度和SHA-256校验通过。文件包含脱敏后的响应与实际传输计时；移除凭据后重新序列化，哈希对应保存文件，**不是原始HTTP字节哈希**。
- 所有SearchHit的access_params_json为NULL；本地响应和SQL导出扫描未发现鉴权值或指定Token/Cookie字段键。原始访问参数只在当前进程内用于详情调用。
- raw可能仍含作者/评论者资料和原文，保留在local_data忽略目录。Git忽略规则测试及已跟踪文件检查通过；本报告只有聚合结果，无真实帖子原文、账户资料或凭据。

## 9. 实际测试与保护核验

| 验证 | 命令或证据 | 结果 |
|---|---|---|
| 原有基线 | `python -m pytest -q`（新增代码前） | 31 passed |
| 新离线测试 | `python -m pytest tests/research -q` | 24 passed |
| 最终完整回归 | `python -m pytest -q` | 55 passed |
| 空库初始化 | `python scripts/phase1c_xhs_smoke.py --init-only` | 18表建立，完整性通过 |
| 真实采集 | `python scripts/phase1c_xhs_smoke.py` | SUCCESS，退出码0 |
| 只读复核 | `python scripts/phase1c_xhs_smoke.py --inspect` | 行数/关系/4个Artifact通过 |
| SQLite | 每连接两项PRAGMA及PRAGMA foreign_key_check/integrity_check | 1/1、空、ok |
| 保护文件 | 本地baseline_hashes及acceptance_audit | 22文件前后哈希一致 |

离线覆盖：六种计数精度、未知时间、PRAGMA、非目标库拒绝、错误Schema拒绝、全部Hit保留、同Attempt重复处理不覆盖、冲突响应拒绝、跨Run相同Post保留多个快照、失败搜索新建Attempt、搜索最多2次、视频详情拒收、评论关系错误导致整个详情批次回滚、匿名一二级关系、凭据脱敏、只读HTTP白名单与Git排除。

首次测试23通过/1失败，原因是Windows文本模式给git check-ignore的stdin追加CRLF，测试误把CR当路径字符；改成NUL分隔的字节输入后24/24通过。真实采集没有HTTP错误或入库错误。未重新执行Phase1B独立192项脚本，其历史结果不计入本次55项。

旧`marketlens.db`前后SHA-256均为`A8EBCE4B1F81EA0B16C3659144660CA3359E1412052DE35AB4A602B96F7A8080`。未修改app.py、旧业务代码、候选DDL/ERD及Phase1B报告。

本地证据索引（均Git忽略）：`logs/experiment_e86bf66eb481466a8b8b95576862d72a.json`、`logs/events.jsonl`、`logs/acceptance_audit.json`、`logs/baseline_hashes.json`。真实响应只能在本地检查，不加入报告附件或版本控制。

## 10. 未实现功能与适用边界

未实现Dataset构建/冻结、人工标注、AI分类、多Run合并、关键词扩展、研究UI、LangGraph、批量采集、评论全量翻页或图片下载。当前支持单进程小规模实验，不宣称已验证多进程并发、进程硬中断恢复和自动修复孤立文件。默认再次运行会追加新Run，不覆盖旧实验；未执行reset。

该结果证明真实采集/持久化链路可运行，未验证样本代表性、评论完整性或推荐质量。技术入库成功不改变既有来源登记中的用途/公开传播状态。

## 11. 下一阶段最优先三个任务

1. **确定Dataset范围与未请求状态**：当前20个候选中17个未获取详情。先明确研究样本、纳入规则、观察截止和未请求候选的表达方式；不能直接把它们映射为detail_unavailable。需要补采或变更表达契约时单独批准，本轮不改Schema、不继续采集。
2. **实现单Run Draft Dataset构建**：在规则确定后选择有效normal Snapshot，关联同Run真实评论Observation，稳定ID评论去重，匿名评论保留；保证候选可追溯、不丢失失败证据。
3. **验收Draft及研究可用性**：核验纳入数量、未请求/失败/类型不符的区别、评论覆盖和缺失指标，增加可重复构建/版本选择测试，再决定是否冻结或进入标注。

第一批真实数据已成功入库；构建Dataset的主要前置问题已从接口连通转为样本与缺失状态规则。按本轮要求停止，等待下一阶段确认。
