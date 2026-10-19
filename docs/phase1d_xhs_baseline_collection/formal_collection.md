# 正式 Baseline：搜索与详情分阶段运行

2026-10-14增强。此次仅实现、离线验证并初始化独立空库，没有执行新的真实搜索/详情，Pilot历史报告及数据保留。

## 两个独立目录

| 用途 | 数据库 | 选择方式 |
|---|---|---|
| 已完成Pilot | `local_data/phase1d_baseline/research_baseline.db` | 默认，或`--pilot` |
| 正式Baseline | `local_data/phase1d_formal_baseline/research_baseline.db` | `--formal`；`--start-baseline`和`--init-formal`自动选择 |

各目录独立保存raw、logs、OS写入锁；正式目录还保存`private_access/*.dpapi`。均被local_data忽略规则排除。没有reset/覆盖选项；启动时若已有Run，默认拒绝新建，须明确恢复。Final Preflight最小修复后，可在显式正式启动时额外指定`--new-project 类别`，在同库建立新Project/Plan/Run；没有该选项仍不会自动追加。恢复按Run保存的Plan读取品类配置。两库均使用原18表DDL，不做Schema迁移，不删除Trigger。

## 命令

项目根目录执行。以下命令是操作说明，不表示已经执行真实采集。

```powershell
# 无参数：仅帮助，不建库、不联网
python scripts/phase1d_baseline_collect.py

# 只初始化/核验正式空库；不会创建Plan/Run或调用服务
python scripts/phase1d_baseline_collect.py --init-formal

# 查看完整50组计划；不联网
python scripts/phase1d_baseline_collect.py --plan

# 唯一的新正式采集启动开关：50组搜索，完成后自动暂停
python scripts/phase1d_baseline_collect.py --start-baseline

# 查看正式库的阶段、搜索完成数量、任务状态、检查点及证据
python scripts/phase1d_baseline_collect.py --formal --inspect

# 另一个终端请求暂停；当前请求正常收尾后不发下一次请求
python scripts/phase1d_baseline_collect.py --formal --pause 1

# 恢复未完成搜索；已成功的查询不重跑；完成后仍暂停
python scripts/phase1d_baseline_collect.py --formal --resume 1 --stage search

# 搜索完成后，显式启动/恢复详情（不再运行搜索）
python scripts/phase1d_baseline_collect.py --formal --resume 1 --stage details

# 用户明确授权后：详情有限自动重试；累计最多5次，等待180–300秒
python scripts/phase1d_baseline_collect.py --formal --resume 1 --stage details --auto-retry --retry-max-attempts 5 --retry-min-seconds 180 --retry-max-seconds 300

# 原Pilot仍只读可查
python scripts/phase1d_baseline_collect.py --inspect
```

`--formal --resume 1`默认只恢复search阶段；不会因搜索已完成而自动继续详情。实际Run ID以首次输出为准。暂停成功或搜索阶段完成返回码0，阻塞返回2，Ctrl+C返回130。Ctrl+C可能打断请求，优先用`--pause`在请求边界暂停。

## 阶段与检查点

`search → search_complete/PAUSED → details → completed`。

SQLite保存每个搜索请求、Task与Attempt的状态。只有请求顺序/数量与Plan完整笛卡尔积一致、所有SearchRequest成功、所有搜索Task有成功Attempt且没有未决Attempt，才认定`search_complete=true`。空库、漏建请求、失败请求和仍在运行的请求均不能放行详情。CLI在预检查前拒绝不完整搜索对应的详情启动；调度器及底层详情调用再次检查。

`logs/checkpoint_<run>.json`原子保存当前阶段、预期/成功搜索数量、搜索完成时间、执行状态与原因。每个调用完成及阶段退出都会更新，`--inspect`展示SQLite推导的实时阶段和保存的检查点。突然断电可能使检查点落后，详情门槛始终以SQLite为准，不相信JSON中的完成标记。搜索完成后Run仍为running且未封口，表示可恢复，不表示还有进程在运行；详情全部结束才completed。

暂停请求保存在`pause_<run>.request`，可在另一个进程写入。当前请求正常完成后保存结果，再于下一次调用前停下。显式resume清除旧暂停请求；不会自动恢复。已提交响应但Task未收尾可自动核对修复；结果未知的running Attempt仍拒绝盲目重放。

## 临时访问参数的跨进程恢复

正式入口先做本机DPAPI加解密自检，再允许真实调用。参数只加密保存在本地缓存，采用Windows**当前用户**范围及禁止弹窗标志，不使用整机共享范围。Windows通常要求同一用户且同一计算机解密；机制依据[Microsoft CryptProtectData文档](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata)。缓存不是免登录凭证，也不是可公开分发的材料。

每个搜索Attempt独立缓存，额外上下文绑定数据库绝对路径、Run和Attempt。处理成功搜索响应时，先原子写入密文，再提交脱敏响应及Hit事务；恢复只读取数据库认定成功的搜索Attempt对应缓存，不信任孤立文件。只存所需笔记访问参数，不复制服务鉴权Cookie或Bearer值。

已用真实Windows DPAPI和synthetic_demo参数验证：结束搜索进程后，新Python进程从缓存恢复5篇详情，0次搜索；磁盘扫描未发现合成明文访问参数。数据库/日志/raw保持脱敏。该测试证明跨进程保存机制，不证明平台参数长期有效。

缓存丢失、被篡改、Windows用户/数据库路径改变或解密失败会明确阻塞；不退回明文存储，不暗中重搜。缓存保存失败仍保存该次真实响应和Attempt，暂停并报告。已取得参数的有效期由平台决定，当前接口没有可靠到期时间，本系统不虚构TTL或保证永不过期。过期/访问限制导致失败时保留证据并停止；需要另行决定刷新请求范围，现阶段不自动刷新。旧Pilot没有密文缓存，不尝试从已脱敏raw恢复已删除参数。

## 验证范围

2026-10-15参数统一：正式请求、Plan和通用达标统计均读取 `BaselineConfig.top_comment_target`，默认20，允许20/25/30。恢复以Run保存的Plan为准，不随后来默认值变化；原 `reached_20_unique` 固定20，新增 `target_unique` 和 `reached_target_unique`。详见[参数重构报告](comment_target_refactor_report.md)。下文JSON的20为默认配置示例。

最新收尾见[统一存储集成报告](storage_integration_report.md)与[存储和恢复说明](storage_layout.md)。用户已接受服务端 DOM 目标 20 的有限滚动协议，并决定保存全部可获取正文图片；[Final Preflight报告](preflight_validation_report.md)及[图片验证报告](comments_images_final_report.md)保留为历史实测证据。图片已经接入正式详情流程，首版AI仅预留文字接口。正式采集尚未启动，等待本次代码与离线测试审核。

本次完整回归：`python -m pytest -q` → **80 passed**（本次新增11项）。正式空库18表、0 Run、0 Attempt、integrity_check=ok；原Pilot、Phase1C及旧商品库前后SHA-256一致。检查证据保存在`local_data/phase1d_upgrade_audit/verification.json`。没有执行真实正式采集。

新增测试覆盖：完整50组搜索后暂停、持久化完成时间、详情门槛、阶段间零交错、请求边界暂停、DPAPI新进程恢复、缓存损坏/上下文错误拒绝刷新、响应提交窗口恢复、默认CLI不建库/不联网、显式正式开关仅搜索、正式库不改Pilot、显式详情不重复搜索。

正式 Baseline 详情现使用 `load_all_comments=true` 和 `comment_config={max_comment_items:20, click_more_replies:false, max_replies_threshold:10, scroll_speed:"normal"}`，通过已有 Adapter 进入 HTTP 请求体。20 是服务端 DOM 滚动目标，不是去重 ID 严格停止点或数据库存储上限。所有自然返回的一级和二级评论均保存，不主动展开回复，不额外请求凑数；每次正式详情的 response_summary_json 记录实际数量、唯一 ID 数、是否达到 20 个唯一一级 ID 和 hasMore。Pilot/Phase1C 保持默认返回模式。正式详情入库后立即处理正文图片，文件与关联分别保存在哈希目录和Manifest；图片失败不回滚详情。可使用同一CLI的 `--formal --resume-media RUN_ID --project-id PROJECT_ID` 独立恢复，或 `--formal --export-run RUN_ID --project-id PROJECT_ID` 导出文字和媒体关联，两者不请求MCP、不写SQLite。正式50组的真实延迟、访问参数寿命及长时服务稳定性尚未实测。


## 2026-10-15：Run 1详情有限重试（待用户授权执行）

以上旧验收中的0 Run属于历史时点。当前只读核验为：Run 1的50组搜索已经完成、464篇图文候选待详情、详情Attempt为0，Checkpoint为search_complete/PAUSED。50份DPAPI缓存可在原Windows用户上下文恢复全部464个访问参数；不保证平台端尚未过期。缺失参数时阻断，不自动重搜。

新增自动重试仅在明确指定`--formal --resume RUN_ID --stage details --auto-retry`时启用。只重试明确记录的HTTPError 500/502/503/504，累计最多5次，随机等待180-300秒，等待每秒检查暂停。历史Plan中的max_attempts=2不变，运行时策略另存日志和Checkpoint。401/403/429、登录不明确、未知进行中请求、本地入库错误及证据损坏均停止。重试前再次检查健康与登录，只在明确就绪时继续，不自动登录。成功详情及评论不足20条都不会补抓，图片继续独立恢复。离线155项全部通过；本轮没有执行真实采集。完整行为、证据和恢复步骤见[详情重试验收报告](detail_retry_report.md)。


## Run 1 Attempt 164 reviewed recovery (2026-10-15)

Current read-only state supersedes the earlier 0-detail snapshot:102 details succeeded,1 failed,361 pending. Attempt164 is a finished TimeoutError/null and remains historically unchanged. Generic auto-retry still blocks unreviewed timeouts. A one-use explicit review can authorize only its next Attempt, with a safe reason, evidence file binding database/Run/Task/Post/Attempt/raw/Checkpoint, and explicit confirmation that the old server request finished. The blocked Checkpoint is archived before progress advances. A new Attempt consumes the decision before I/O; another timeout requires a new review. Prior attempts still count towards5. `--detail-timeout-seconds 120` is optional and formal-detail-only; other operations keep their previous timeout. No new collection has been executed in this review task. See [Attempt164 review and exact commands](attempt164_timeout_recovery_report.md). Final offline regression:186 passed,0 failed,0 skipped. Four databases, DDL, all606 formal files unchanged.


Runtime interval update (2026-10-15): use explicit `--retry-min-seconds 30 --retry-max-seconds 60` for new waits. Existing persisted waits retain their original value and deadline even when outside the new interval; defaults remain180-300 for compatibility. Attempt164 review and120-second detail timeout remain unchanged. See [30-60 pre-resume report and full command](retry_30_60_pre_resume_report.md). No recovery was executed.


Source-deletion review (2026-10-15): Run1/Task153/Post108 has two recorded timeouts and a user-reported deleted source. Explicit local-only confirmation now records source_deleted, time, bound evidence and original Checkpoint, then marks this Task cancelled. No HTTP call or automatic resume occurs. Only a valid review permits skipping; cancelled alone is not sufficient. Candidate count stays464, confirmed unavailable is separate from detail success, and final Run closes partial if one source remains unavailable. Existing30-60 retry and120-second detail timeout commands remain applicable to all remaining tasks. See [source-deletion review and confirmation/resume commands](source_deleted_skip_report.md). This change has not been applied to formal data.


Generic pending review (2026-10-15): explicit --defer-detail-attempt and --reactivate-detail-attempt local actions reuse original tasks and immutable evidence. Manual classifications are comment_loading_stalled/detail_timeout_unknown, separate from source_deleted. Deferred tasks are partial, never successful or permanently cancelled. Pending work is reported separately; resultPARTIAL remains an unsealed/running SQLite Run for later refill, since sealed Runs cannot reopen. Follow [pending review report](pending_review_report.md) for exact Attempt249=Task237/Post192 commands, stats, lifetime budget and activation rules. Real Run1 has not been labeled or resumed in this task.
