# Phase 1D 详情请求节奏控制（2026-10-16）

已实现 MarketLens 调度层的正式详情运行时节奏控制，不修改研究 Plan、数据库结构或上游 MCP。未启动任何真实请求、未恢复 Run 1。

## 执行顺序与配置

正式 CLI 使用 `--formal --resume RUN_ID --stage details` 时默认启用 `DetailPacingPolicy`：

1. 保留 Run 配置中的固定等待（Run 1 为 3 秒），再加随机缓冲 U(0,10) 秒。
2. 发出单篇原有详情请求，按原逻辑保存全部实际返回的正文、评论并处理独立媒体恢复。
3. 详情成功提交后，额外随机等待 U(5,10) 秒，再允许进入下一任务。

正常两次详情任务之间的调度等待为 U(5,10)+3+U(0,10)，即 8–23 秒，期望 15.5 秒；请求耗时、SQLite 保存和媒体处理时间另外计算。第一篇只有前置 3–13 秒等待。最后一篇也执行完成后的等待。

新增参数集中于 `DetailPacingPolicy`，不进入 `BaselineConfig.persisted()`，因此 Run 1 原 Plan/config 一致性检查不变。CLI 支持：

- `--detail-buffer-min-seconds` / `--detail-buffer-max-seconds`，默认 0 / 10。
- `--detail-after-min-seconds` / `--detail-after-max-seconds`，默认 5 / 10。
- `--no-detail-pacing`：关闭新等待安排，保留原固定间隔；已经持久化的前置/后置等待仍须遵守。

Python 调用通过现有 `BaselineCollector(..., detail_pacing_policy=DetailPacingPolicy())` 显式启用。Pilot 不支持该策略，搜索阶段不使用该策略。没有修改浏览器生命周期或评论协议。

## 暂停、恢复与证据

复用现有暂停文件、原子文件写入和本地日志目录。每次随机等待保存绑定 Run/Task/attempt_no/阶段的安排和最早继续时间；不会在暂停、重启或配置变化后重新抽取。每次 sleep 最多 1 秒，等待中可暂停，进程停止期间经过的时间也计入等待期限。

`logs/detail_pacing_RUN.jsonl` 追加采集耗时（请求至提交及媒体处理）、HTTP 耗时、成功/失败状态，以及各等待段本次进程实际等待时长、截止时间是否达到。等待安排为 `logs/detail_pacing_wait_TASK_ATTEMPTNO_before|after.json`。不记录正文、Cookie、Token 或认证头。

已成功但尚未完成后置等待的任务，恢复时只完成剩余等待和原媒体恢复，不重新请求详情。耗时日志失败会阻断，不会为补日志重复请求已提交详情。

## 阻断与兼容性

现有 TimeoutError/null、401/403/429、未知进行中请求、入库错误及人工审核绑定规则不变。补充识别失败响应中的明确访问限制、访问频繁、安全验证、access denied、rate limit、too many requests 和 300013 提示，即使外层为 HTTP 500，也不自动重试。

明确 HTTP 500/502/503/504 仍沿用现有可选有限重试和持久化等待安排；本改动不改变其等待区间或累计预算。Run 1 的 30–60 秒和累计 5 次仍由既有重试参数指定。每次重试前也遵守固定间隔及随机缓冲；失败不执行成功后的 5–10 秒等待。任务未解决时不会继续下一篇，预算耗尽会 BLOCKED，并保留 Checkpoint。

## 验证

使用内存 SQLite、合成 Adapter 和模拟时钟，禁止真实网络。命令设置 `MARKETLENS_TEST_MEMORY_ONLY=1`，运行以下模块后恢复原环境变量：

`test_detail_pacing.py`、`test_detail_auto_retry.py`、`test_pending_review.py`、`test_source_deleted_review.py`、`test_final_preflight.py`、`test_storage_integration.py`、`test_comments_images_preflight.py`。

最终结果：**227 passed，5 skipped，0 failed**，13.00 秒。5 项为需要独立磁盘数据库和子进程的已有测试，没有将跳过计为通过。新节奏的恢复测试通过重新构造 Collector 并读取同一临时日志模拟重启，不声称本轮执行了真实跨进程 SQLite 恢复测试。

覆盖默认等待总量、参数校验、前后等待中暂停、复用已保存安排、关闭策略后的未完成等待、成功任务去重、累计预算、未知超时与平台限制阻断、日志损坏阻断、媒体失败不重复详情、CLI 参数作用域，以及历史审核证据不被拒绝操作改写。既有评论目标 CLI 测试增加虚拟时钟，避免真实等待，保留全部原断言。

语法检查和本轮修改文件的 `git diff --check` 通过。工作区原有未提交修改保留，包括原先已修改的 Schema 文件；本轮没有修改 Schema 或 Trigger。没有 Commit/Push。

测试前后校验正式目录全部文件、工作区既有 `.db` 和现有 DDL 文件，共 1264 个文件，逐文件 SHA-256 清单的聚合摘要均为 `fe85aef114e4b6eb968f07ac706b523db3f17b4f3b874e6c99b64e9dfef95956`。正式数据库、Checkpoint、历史 Attempt、raw、评论、媒体与 DPAPI 缓存均保持不变，没有新增研究数据库。

## 可选批次休息补充（2026-10-16）

在同一个 `DetailPacingPolicy` 中新增批次参数，默认 `batch_enabled=False`。正式详情恢复时添加 `--detail-batch-rest` 才首次启用；不自动启动任何 Run。默认每批目标通过 `randint(15,30)`（含端点）抽取，完成该批目标数量的成功详情任务后，额外等待 `uniform(60,300)` 秒，再抽下一批目标。既有固定 3 秒、随机缓冲 0–10 秒、成功后等待 5–10 秒全部保留。即使最后一篇恰好达到批次目标，也执行该批休息。

可配置参数：`--detail-batch-min-tasks`、`--detail-batch-max-tasks`、`--detail-batch-min-seconds`、`--detail-batch-max-seconds`；这些参数要求同时提供 `--detail-batch-rest`。批次配置不能与 `--no-detail-pacing` 同时指定。Python 策略对象使用对应的 `batch_*` 字段。配置不进入研究 Plan，也不改变详情 HTTP 超时或评论协议。

持久化和恢复：

- 复用 `logs/detail_pacing_RUN.jsonl`，追加 `batch_started`、`batch_counted`、`batch_rest_scheduled`、`batch_rest_completed` 状态事件，包含启用时的 Attempt 水位、批次编号、目标、已完成数量、已计数 Task ID、原运行时批次参数及休息截止时间。没有新增数据库或另一套调度入口。
- 首次启用记录当前最高 Attempt ID；此前已成功详情全部排除。其后按已提交成功 Attempt 对应的唯一 Task 计数，失败和重试不增加批次完成数。失败/重试证据仍保存在原 Attempt、重试日志和采集耗时日志中。
- 如果成功已经提交，但进程在批次计数前退出，恢复时从数据库查询成功记录与日志水位对账，不重新请求详情，也不漏计或重复计数。
- 同一追加事件同时保存成功计数和休息安排，防止出现计数已满却丢失随机等待值。休息复用 `_pacing_deadline()`，每次最多 sleep 1 秒并响应现有暂停文件；日志记录每段实际等待时间。停机期间经过的时间计入截止时间。
- 已启用的批次会持续按日志中的参数恢复：遗漏启用开关、关闭新单篇等待或修改程序默认参数都不会丢弃既有批次状态。不会悄悄重抽目标或跳过已有休息。需要改变已启用批次策略时，应另行明确设计转换，不能直接改写日志。
- 未知进行中请求在休息前立即阻断。其他超时、401/403/429、访问限制、入库错误和现有人工审核规则保持有效；批次休息不会清除失败或授予恢复权限。日志截断、绑定或计数证据异常时阻断并保留 Checkpoint。

新增 `tests/research/test_detail_batch_pacing.py`，21 项通过。覆盖默认 15/30 篇边界、60–300 秒抽样、额外休息叠加、休息中暂停、原目标及截止时间复用、历史成功排除、成功提交后的计数恢复、重试只计一次、失败/未决请求阻断、日志截断拒绝、参数与 CLI 校验，以及真实子进程读取同一临时日志恢复且不重复休息。子进程只重建合成的内存 SQLite，不创建磁盘研究库或访问网络。首次子进程测试遇到 Windows 命令行长度限制，改为通过 stdin 传入合成测试程序后通过，未降低断言。

连同原节奏、重试、人工审核、暂缓、评论和媒体回归，最终为 **248 passed、5 skipped、0 failed（14.48 秒）**。5 项跳过仍为已有磁盘/子进程测试，不计为通过。语法及本轮修改文件的 `git diff --check` 通过。

本轮修改：`baseline_config.py`、`baseline_collector.py`、`phase1d_baseline_collect.py`、新增批次测试及本报告、README、WORK_LOG。没有修改 Schema、Plan、上游 MCP 或历史 Attempt；保留其他用户未提交修改。

本轮前后正式目录、既有 `.db` 和 DDL 共 1265 个文件的聚合摘要均为 `249100d6f438a1db4d15964542c6ba019614b91097883c7dda540779e6356efa`。与前一轮文件数不同是本轮开始前已有文件变化，本轮前后清单一致；未修改正式数据、Checkpoint、raw、媒体或 DPAPI 缓存。未发真实请求、未恢复 Run 1、未 Commit/Push。

恢复前仍须处理：只读核验 Run 1 最新为 **Attempt 315 / Task 302 / Post 257 / attempt_no=1 / TimeoutError / http_status=null**，Checkpoint 仍为 **BLOCKED / UnsafeFailureNeedsReview;no_automatic_retry**。独立验证成功不等于已恢复原任务；仍需按现有精确绑定的一次性超时审核机制授权该失败，或明确人工暂缓，并再次明确授权正式恢复。不能直接靠新增批次开关绕过该阻断。
