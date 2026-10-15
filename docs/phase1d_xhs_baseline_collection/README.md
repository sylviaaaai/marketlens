# Phase 1D — 粉底液基准语料采集

复用 Phase1C 的 HTTP Adapter、标准化、事务入库和 Artifact 审计。保留原18表和全部 Trigger。原Pilot库为 `local_data/phase1d_baseline/research_baseline.db`，正式库为 `local_data/phase1d_formal_baseline/research_baseline.db`，两者隔离。历史实验结果见 [pilot_report.md](pilot_report.md)，最新两阶段操作说明见 [formal_collection.md](formal_collection.md)。

## 运行

在项目根目录执行：

```powershell
$env:PYTHONIOENCODING='utf-8'
python -m pytest -q
python scripts/phase1d_baseline_collect.py --plan
python scripts/phase1d_baseline_collect.py --inspect
```

`--plan` 仅打印完整50组，不发请求；无参数只显示帮助。正式采集必须显式指定 `--start-baseline`，仅运行搜索阶段并暂停；详情另用 `--formal --resume 1 --stage details`。首次授权Pilot可执行 `--pilot`；已有Run时拒绝另起实验。已关闭Run只读返回，不发健康/登录/采集请求。正式库选择器为`--formal`，默认仍指向Pilot。此次增强只做离线测试与空库初始化，没有启动真实正式采集。

## 固定设计

完整配置按关键词外循环、排序内循环生成10×5组合。顺序：使用感受、测评、踩雷、推荐、种草、回购、盘点、对比、品类本身、购买；排序为综合、最新、最多点赞、最多评论、最多收藏。更换 `BaselineConfig.category` 可复用模板。

Pilot 为 `粉底液 × 综合/最新`，再 `粉底液测评 × 综合/最新`。每组保存首次成功单页的全部有效 Hit；失败调用可有限重试，不做第二页、成功刷新或条数补齐。Post 稳定身份去重，Hit 来源全部保留。候选按照首次搜索顺序和返回位置排列，最多选择前5篇 normal 图文候选，不使用任何价值评分。每个 normal 候选都有详情 Task；没有 Attempt 即为未请求。Pilot范围外 Task 收尾为 cancelled，但不解释为详情失败。

## 评论能力结论

- `load_all_comments=false` 跳过主动滚动，读取页面已经加载的评论；不是20条存储上限。
- 上游 v2.5.5 提供一级目标加载：MCP 为 `load_all_comments=true, limit=20`；HTTP facade 要用嵌套 `comment_config.max_comment_items=20`，不能传 MCP 的扁平参数。该机制在同一次详情调用内滚动，没有独立评论 cursor 接口。
- `reply_limit` / `max_replies_threshold` 是针对回复按钮的跳过阈值，不是每篇累计10条的追加预算；不能声称实现了20+10。
- 因缺少完整的每篇回复预算控制，本轮使用默认返回模式，无追加评论请求、无全量展开、无硬截断。一级20/二级10仅保存为研究目标。一级有界滚动仅完成源码核对，本轮没有启用或实测。

依据：[已部署工具接口相应的上游请求结构](https://github.com/xpzouying/xiaohongshu-mcp/blob/v2.5.5/types.go)、[HTTP 参数转换](https://github.com/xpzouying/xiaohongshu-mcp/blob/v2.5.5/handlers_api.go)、[评论加载实现](https://github.com/xpzouying/xiaohongshu-mcp/blob/v2.5.5/xiaohongshu/feed_detail.go)、[搜索排序与筛选](https://github.com/xpzouying/xiaohongshu-mcp/blob/v2.5.5/xiaohongshu/search.go)。已核对当前会话暴露的 search_feeds/get_feed_detail 定义以及本地数据字典；实际采集复用 HTTP facade，不走 tools/call。版本号不是容器二进制与上游源码完全一致的证明。

## 检查点与恢复边界

1. 创建完整搜索请求/任务清单，Plan 在创建 Run 后保持不可变。每次调用前提交 running Attempt，响应仍通过 Phase1C 原子事务写入。
2. 任一错误立即暂停；Run 留在 running，`finished_at=NULL`，表示尚未封口，不代表进程仍在运行。CLI 的 OS 文件锁防止同一入口并发写入，进程退出自动释放。
3. 明确记录的普通500失败可显式恢复，单任务累计最多2次 Attempt，预算不随进程重启重置。401/403/429、无HTTP状态或入库错误拒绝自动重试。已成功 Task 永不再次调用。
4. 响应事务已提交但 Task 尚未收尾时，可从已完成 Attempt 修复任务状态，不重复调用。仍为 running 的 Attempt 无法确定是否已发请求或收到响应，停止并报告人工审计需要；不伪造失败，也不盲目重放。
5. 正式入口使用Windows当前用户DPAPI，把每次成功搜索的临时参数保存到`private_access/*.dpapi`加密缓存，重启后读取同Run成功Attempt对应文件。缓存写入先于搜索证据事务提交，SQLite、raw和日志仍不保存明文参数。已验证新Python进程无需重搜即可继续详情。缓存缺失、损坏、用户/数据库上下文不匹配则阻塞；参数过期导致接口失败也停止，不暗中刷新。原Pilot仍保持原来的内存模式，既有数据不改写。
6. 不修改原库或删除响应；日志追加。不存在 reset 或 Dataset 构建/冻结功能。

7. 搜索阶段完成条件由SQLite中固定Plan的全部关键词×排序请求、成功Task/Attempt联合核验；详情阶段及底层详情调用均有门槛。搜索完成时间和暂停状态保存在`logs/checkpoint_<run>.json`，检查时仍以SQLite推导状态为准，状态文件不授予详情执行权限。`--pause RUN_ID`在当前请求结束后暂停；`--resume`显式清除暂停请求。

成功的Pilot Run关闭后不能重开或扩展为正式采集；正式活动使用独立Plan/Run和独立库。完整50组调度及跨进程恢复仍仅用synthetic_demo验证；正式运行需要用户明确启动。
