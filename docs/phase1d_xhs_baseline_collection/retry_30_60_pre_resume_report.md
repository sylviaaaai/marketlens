# Run 1：30–60秒重试与恢复前检查

日期：2026-10-15。仅代码、离线测试及只读核验；没有执行恢复或发送任何网络请求。

## 检查与最小修改

原代码不能直接接受30–60秒。CLI本身接收浮点数，但 `DetailRetryPolicy` 强制180秒下限；Collector读取等待文件时也强制180秒下限。

仅修改两处生产逻辑：

- `baseline_config.py`：允许显式运行时等待区间在30–300秒内，新增30–60可用；五次上限不变。
- `baseline_collector.py`：等待记录允许30–300秒，兼容旧180–300记录；不改任何已有等待文件。

默认180–300保留，恢复命令显式指定30–60。CLI、Adapter、审核机制、研究Plan、Schema及Trigger没有修改。正常间隔仍为原Plan保存的3秒；详情120秒仍由已有运行时选项指定。运行时策略与原Plan精确一致性校验独立，原Plan的max_attempts=2不被改写。

## Run 1只读状态

- Checkpoint仍为 `details / BLOCKED / UnsafeFailureNeedsReview;no_automatic_retry`，其中历史运行时策略180–300完整保留。
- 详情任务102成功、1失败、361待请求；Attempt164仍为任务153/笔记108的第1次失败，TimeoutError/null，仍是该任务最新Attempt。没有running Attempt。
- `detail_retry_1.jsonl`共208条事件，末条对应164的安全阻断。
- 只有一份历史等待文件，对应Attempt62，随机值258.1474048477828秒；同一任务已存在更高编号Attempt，故该等待已消费。**未消费等待安排为0**；Attempt164没有等待安排。
- 原等待文件的随机值、安排时间及最早重试时间均未覆盖、删除或重新抽取。即使后来恢复的是未消费的旧等待，新策略也复用其原始期限，不缩短、不重抽。
- Attempt164证据文件中的数据库/Run/Task/Post/Attempt/原次数/结束时间/raw哈希绑定均匹配；原Checkpoint哈希仍匹配，raw文件哈希正确。`detail_review_164.json`尚未创建，审核授权没有被消费。

30–60的新运行时区间不进入研究Plan，也不参与审核证据的Checkpoint哈希计算，因此不使原证据失效。只有未来显式执行审核恢复时才创建审核记录并完整归档原Checkpoint，随后进度Checkpoint才允许前进。本轮未运行该入口。

TimeoutError/null仍需精确绑定的一次性人工审核，不能视为500；新的超时不会继承164的授权。401/403/429、登录异常、未知结果、进行中请求及入库错误的原阻断规则不变。人工审核后的首次重试也沿用同一运行时等待策略，因此在该命令下为30–60秒。

## 离线验证

六个相关模块：baseline_collection、baseline_stages、detail_auto_retry、comments_images_preflight、storage_integration、final_preflight。

最终 **196 passed，0 failed，0 skipped，32.90秒**；其中重试模块85项，本轮扩充10项测试情形。验证30–60传入随机取值函数并使用45秒合成等待、四种可重试状态、累计五次耗尽、新进程继承已有两次后最多再尝试三次、原长/短等待记录逐字节不变、原Plan不变、一次性审核与30–60/120秒CLI组合、成功任务跳过及详情专用超时隔离。所有等待和HTTP均为模拟。

隔离测试使用继承至子进程的审计钩子，禁止真实网络，禁止临时目录外SQLite连接和写入。临时根目录 `C:\Users\owner\AppData\Local\Temp\marketlens_phase1d_disk_4449wqlt` 已清理，未新增长期数据库。审计及测试输出位于被Git忽略的 `local_data/phase1d_retry_30_60_validation/`。

四个既有数据库、DDL、正式目录全部606个文件、原审核证据文件前后SHA-256一致；包含Plan、历史Attempt、Checkpoint、旧等待、评论、媒体、缓存及Manifest。其他未提交修改保留，未Commit/Push。

## 可复制命令（未执行，须用户明确授权）

以下命令保留原审核理由，要求操作者已确认服务端旧请求结束。它会真实恢复失败笔记并继续后续候选，不是离线检查。登录及访问参数有效性本轮未在线验证；现有预检查和缺失参数阻断仍有效。

```powershell
Set-Location 'E:\Project\marketlens'; python scripts/phase1d_baseline_collect.py --formal --resume 1 --stage details --auto-retry --retry-max-attempts 5 --retry-min-seconds 30 --retry-max-seconds 60 --detail-timeout-seconds 120 --approve-timeout-attempt 164 --review-reason "Reviewed Attempt 164 evidence; confirmed old server request finished; authorize one further detail attempt without reclassifying the timeout" --review-evidence local_data/phase1d_attempt164_review/attempt164_evidence.json --confirm-server-request-finished
```

现有1次计入五次预算，最多剩4次。授权消费后不能用旧审核参数重放；再次超时需另行审核。若执行前正式Run或证据已变化，必须重新只读核验，不能修改历史来匹配。本轮完成后停止，等待用户授权。
