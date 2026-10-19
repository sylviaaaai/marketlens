# Phase 1D：显式离线封口

状态：实现、隔离验证及用户授权的正式封口均已完成。**正式 Run 1 已于 2026-10-19 11:07:12.731（Asia/Shanghai）关闭为 partial**。

Run 1 已完成全部计划任务的处理，但只有 462/464 篇详情成功：Task153 来源删除；Task473 人工确认被封禁或限制访问，永久不再补采。Task237/300/301 已由 Attempt531/532/533 成功补采。来源删除、受限跳过和成功采集严格分开。

## 为什么需要独立入口

既有 `collection_tasks.partial` 和 pending_review 文件表达的是“等待人工复核/可显式补采”。采集器发现此状态时刻意保留 `collection_runs.running`。现有来源删除确认只适用于 Run1/Task153/Post108，不能用来处理受限帖子。

现有 18 表无需迁移：任务保留原状态；新增不可覆盖的本地终结记录表达永久不补采；Run 以现有合法 `partial` 关闭。全部成功的其他正式 Run 也可用同一入口关闭为 `completed`。失败或未审核任务不会因为总数相符就被接受。

已核对实际 SQLite 定义：Run 状态允许 pending/running/completed/partial/failed/cancelled；Task 允许 pending/running/succeeded/partial/failed/cancelled；Attempt 允许 running/succeeded/partial/failed/cancelled。Task 的 partial 本身不是“永久跳过”的充分证明，必须结合审核及终结记录。`trg_run_close_clean` 只允许没有 pending/running 请求/任务或 running Attempt 的 Run 关闭；`trg_run_no_reopen` 禁止任何已关闭 Run 更新。`trg_attempt_update` 禁止修改已完成 Attempt，Run 关闭后 Task/Attempt 均不可再更新，历史也不可删除。

Checkpoint 是原子替换的 JSON，不是 SQLite 状态枚举；既有 lifecycle 使用 stage 和 execution_status 表达搜索/详情/等待/阻断/暂停/结果。原普通调度会在返回结果后写 Checkpoint。本地封口不调用这条路径，避免故障后普通 resume 将封口前 Checkpoint 覆盖；原 JSON 全文保存在不可覆盖的封口意图中。

## CLI

在项目根目录执行。以下真正封口命令须明确授权；2026-10-19 已按用户授权对正式 Run 1 执行，重复同一命令会核验并返回 ALREADY_FINALIZED：

```powershell
# 只读检查；不创建封口记录，不写数据库或 Checkpoint
python scripts/phase1d_baseline_collect.py --formal --check-finalization 1 --finalization-decision local_data/phase1d_local_finalization_validation/run1_finalization_decision.json

# 用户另行确认后，显式本地封口；禁止混用采集、认证、重试选项
python scripts/phase1d_baseline_collect.py --formal --finalize-run 1 --finalization-decision local_data/phase1d_local_finalization_validation/run1_finalization_decision.json

# 封口后的只读状态和一致性验证
python scripts/phase1d_baseline_collect.py --formal --inspect
python scripts/phase1d_baseline_collect.py --formal --check-finalization 1 --finalization-decision local_data/phase1d_local_finalization_validation/run1_finalization_decision.json
```

决定文件位于被 Git 忽略的 local_data，包含 version、database_path、run_id、reason、checkpoint_sha256、permanent_skips。每条永久跳过必须明确绑定 task_id、post_id、attempt_id、原审核文件 SHA-256、原审核证据 SHA-256，指定 classification=`banned_or_restricted`、no_refill=true 和人工说明。未知字段、孤立/过期决定或来源删除冒用均拒绝。

决定文件是用户结论的可审核输入，不是平台自动判定；不得将 Cookie、Token、原始评论或账号材料放入其中。此轮已准备 Run 1 决定文件并通过正式库只读检查；若证据或 Checkpoint 后续变化，旧决定会被拒绝，不能修改历史来配合旧绑定。

## 验证边界

封口执行持有原采集器 OS 单写者锁，并以 SQLite BEGIN IMMEDIATE 排除活跃数据库写者。只读预检是快照检查，真正执行仍会在锁内重验。

- 搜索请求必须与保存的关键词×排序计划逐组一致，所有任务、请求和 Attempt 必须确实结束。
- 根据 search_hits 中真实 normal 候选核对详情任务身份集合，不能漏任务或只看总数。
- 每个成功任务必须有唯一成功 Attempt；成功详情必须有相同 Post/Attempt 下的唯一快照。
- 所有 Attempt 的 raw 证据必须存在且 SHA-256 匹配；历史失败不删除、不覆盖。
- 复用原 pending_review/source_deleted 的绑定校验，检查 activation、原 Checkpoint、外部审核证据和超时恢复决定消费情况；未处理决定拒绝。
- 每个未成功任务必须是有完整来源删除审核的 cancelled，或有精确永久终止决定的 partial；failed/pending/running/无审核 cancelled 拒绝。
- 封口仅更新 collection_runs.status、finished_at、error_summary；Task、Attempt、raw、Plan、成功数据和 Schema 均不变。
- finished_at 是实际封口时间，不倒填采集结束时间。

模块不构造 Adapter、MediaStore 或认证缓存，不读取认证、不解密 DPAPI、不启动/连接 Docker、不进行 preflight、MCP、HTTP 或 DNS 请求。`--inspect` 不会封口；普通 resume 不会代替封口。

## 写入协议、不可逆性和恢复

1. 锁内完整验证，生成本 Run 范围的采集证据指纹及所有审核/raw 文件哈希。
2. 将完整、fsync 后的 `logs/finalization_<run>.json` 通过原子、不覆盖的发布方式保存。内含输入决定、原 Checkpoint 全文、封口时间、逐任务 disposition、最终统计和关闭前状态。创建失败不留下半份有效意图。
3. 同一 SQLite 事务把 Run 关闭为 partial/completed，并在 error_summary 保存 `local_finalization_v1:<意图SHA-256>`，使不可变数据库锚定终结记录。其他表不写。
4. 原子生成最终 Checkpoint：stage=completed（阶段结束），execution_status=PARTIAL/SUCCESS（采集结果），run_sealed=true、finished_at、终结记录哈希和最终统计。原重试/节奏策略保留。

数据库和文件不能跨介质原子提交，因此意图先行：

| 故障位置 | 实际状态 | 恢复行为 |
|---|---|---|
| 发布意图前 | Run 和 Checkpoint 未变 | 修复本地错误后重试 |
| 意图已发布、数据库提交前 | Run 开放，封口意图存在 | 普通 resume 在认证/网络/Checkpoint 写入前阻断；重跑相同显式封口命令完成关闭 |
| 数据库提交后、Checkpoint 更新前 | Run 已关闭，Checkpoint 旧 | 数据库禁止重开；重跑相同命令仅核验并修复 Checkpoint |
| 全部完成 | Run 和 Checkpoint 终态一致 | 同一命令返回 ALREADY_FINALIZED，不创建重复记录、不改时间或文件 |
| 意图、证据、数据库锚或 Checkpoint 被额外改变 | 检测到冲突 | 明确拒绝，不静默覆盖或改写证据；需要人工诊断 |

重跑要求原决定内容完全相同。普通 resume 对已关闭 Run 在 initialize、认证读取、Adapter、DPAPI 和 preflight 前返回 ALREADY_CLOSED；对开放但存在封口意图的 Run 返回 BLOCKED。直接 Collector.run 同样阻断且不改 Checkpoint。

`--inspect` 暴露 local_finalization.sealed、checkpoint_synchronized、recovery_required 或校验错误，不负责自动修复。封口完整且数据库锚一致后，Task473 在汇总中计入 banned_or_restricted/permanent_skip_count，不再计入 pending_review；其数据库状态仍是原 partial。Task153 继续单独计 source_deleted/confirmed_unavailable。

指纹限定本 Run 的采集证据；后续添加 Dataset v1 或其他 Run 不会使已封口记录失效。关闭后的 Task/Attempt 受现有 Trigger 保护，Run 不可重新打开。后续补充评论必须另建采集任务/Run，不修改 Run 1。

## 预期 Run 1 结果

- Run status=partial，finished_at 为执行时间；Checkpoint completed/PARTIAL、run_sealed=true。
- candidates=464、search_succeeded=50、succeeded=462、source_deleted=1、banned_or_restricted=1、skipped=2、pending_review=0。
- failed=0 表示没有未解决的失败任务，不表示历史没有失败：historical_failed_attempts=21 保留原记录。
- Task237/300/301 的成功身份和快照由通用逐任务校验核实，不靠硬编码总数授权。

## 验证

新增离线测试覆盖正常全部成功/部分成功封口、未完成任务/请求/Attempt、缺少候选任务、虚假成功标志、未处理审核、错绑/过期/重复决定、raw 损坏、锁冲突、不可变性、幂等、五个提交边界故障恢复、Checkpoint 冲突、封口后 resume、后续 Dataset/Run 兼容。测试禁用真实 socket、子进程和 DPAPI。

正式隔离副本使用 SQLite backup API 创建，保留相同 18 表与全部 Trigger；仅在副本中重绑定复制的 8 份审核证据路径/哈希。原始 raw 以只读方式核验；未调用媒体恢复或读取 DPAPI。隔离副本通过正常封口、幂等和封口后 resume，最终462/1/1、历史失败21，integrity_check=ok、foreign_key_check=0。审计钩子记录网络、子进程、越界写入均为0。

实际输出与正式数据前后哈希清单保存在 `local_data/phase1d_local_finalization_validation/`；这些研究资料、决定及隔离数据库不得加入 Git。

本轮最终实测：新增测试 **30 passed**；`python -m pytest tests -q --tb=short` **384 passed，42.68 秒，无失败或跳过**。隔离副本最新验证目录为 `local_data/phase1d_local_finalization_validation/isolated_20261019T105908719715`。正式库及现存正式目录/外部审核证据共 **2752 文件**前后大小与 SHA-256 完全一致，正式文件集合未增删；源库仍 running/finished_at=NULL，integrity_check=ok、foreign_key_check=0。`git diff --check` 通过，仅存在既有 LF/CRLF 提示。

以上是实现/隔离阶段的历史验收，下面记录后续用户授权的正式执行。

## 正式执行结果：2026-10-19

执行前重新确认无采集/写库进程或未结束 Attempt；成功取得 OS 采集锁及排除数据库写句柄的 Windows 保护。原独立备份 2756 项 SHA-256 全部通过，正式源文件与该备份时的 source SHA-256 相同。正式决定文件绑定真实正式数据库、Run1/Task473/Post446/Attempt489 和既有审核证据，非 synthetic fixture；SHA-256 为 `97d8579bfe73d7039a691be3b1a2b8f1fb93364825bf6ba52ce6b46de77cbb4e`，执行前后未变。全部逐任务验证通过后，才通过原 CLI 执行正式封口。

正式结果：Run=partial；finished_at=`2026-10-19T03:07:12.731Z`；Checkpoint=completed/PARTIAL；run_sealed=true、checkpoint_synchronized=true、recovery_required=false。搜索成功50、详情成功462、source_deleted=1、banned_or_restricted=1、pending_review=0、历史失败 Attempt=21。Task153/473 原状态 cancelled/partial 不变；Task237/300/301 原成功结果不变。

不可覆盖的正式记录：`local_data/phase1d_formal_baseline/logs/finalization_1.json`，SHA-256：

`b3b9f001e99cc5fd13cd1ae9e2534cfffa874554d74356d9f9008a4f64652e0d`

数据库 error_summary、Checkpoint.finalization_sha256 与记录哈希一致。执行及 inspect/check-finalization 的审计显示网络、子进程调用均为0；未进行认证读取或 DPAPI 解密。正式库 integrity_check=ok、foreign_key_check=0；Schema 完全不变，18 表中仅 collection_runs 内容变化。

正式根目录仅修改 `research_baseline.db`、`logs/checkpoint_1.json`，新增 `logs/finalization_1.json`；其余文件哈希不变，无正式文件删除。数据库内 Task、Attempt、快照、评论、raw 关联等原数据全部不变。

封口后独立备份：`E:\MarketLens_Backups\MarketLens_Phase1D_Run1_POST_FINALIZATION_20261019T110803+0800`。

- 数据/证据载荷：2753 文件，204392611 字节，含全部正式资料、50 份单独保存的 DPAPI 密文、8 份外部审核证据及原样决定文件。
- 含清单/说明后：2759 文件，206246163 字节。
- SQLite backup API；完整逻辑内容相同、integrity_check=ok、foreign_key_check=0；源在备份期间未变化。
- SHA256SUMS.txt 的2758项再次独立核验，0错误；清单自身哈希 `6e865a4af74862712088290e2e653a384c940fc7925e74050ff07d59b9f8100d`。
- 封口后备份中的数据库锚、终结记录和终态 Checkpoint 一致。目录权限限定当前 owner 和 SYSTEM。
- 封口前备份原样保留，并再次完整验证通过，未覆盖。

执行日志、前后表级哈希和文件变化清单、两个备份的独立验证结果保存在 `local_data/phase1d_local_finalization_validation/formal_execution_20261019/`。数据保护和正式终态前置条件已满足，可进入 Git Checkpoint A 文件清单审核；尚未暂存、Commit、Push、冻结 Dataset 或清理目录。
