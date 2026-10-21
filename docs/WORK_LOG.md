# MarketLens Work Log

## 2026-10-09 — Product/architecture handoff

- Scope set to consumer-oriented shopping research rather than merchant product selection.
- First test vertical: moisturizer, with generalizable product architecture.
- Four-stage user journey defined: Discover → Match → Analyze → Recommend.
- MVP and follow-up features separated; real source permissions remain unverified.
- Technical specifications and staged Codex instructions written.
- **No new consumer MVP modules are claimed to be implemented at this stage.**

### Future entry template

Date / Gate / What changed / How to run / Tests actually executed / Data provenance / Limitations / Next actions

## 2026-10-14 — 实验文件整理

- 按用户要求核对旧设计日志：v0.2～v0.6相对v0.7仅版本行被替换，正文全部保留于v0.7；删除这5份重复旧文件。
- 删除项目内7个Python/pytest缓存目录，不动.venv、真实数据、原始审核材料或运行代码。
- 根README加入完整实验目录导航；填写原本为空的Phase1B README索引。未新增清理报告或版本副本。
- 保留仍被回归脚本读取的v0.2输入和历史报告；保留本次4份脱敏响应与4份日志。只读核验实验库及Artifact，不重新采集。

## 2026-10-14 — Phase 1D Final Preflight集中验收

- 用户限定预算且禁止新建SQLite文件。读取当前规则、Pilot/正式说明、Adapter/Normalizer/采集器/Schema与上游v2.5.5源码；通过原CLI的日志专用验收动作执行有限真实调用，不建研究Run。
- 真实五种排序各成功1次，共6次搜索（最多点赞首次500后一次成功重试）；每组20条normal、100条命中去重79篇。点赞/评论/收藏对应指标19对相邻值均非递增；最新无发布时间字段，语义PARTIAL。
- 两篇默认10条一级，有限目标20均返回20个唯一一级ID；二级分别4→11和0→0，全部保留。目标30诊断500，未取得超过20一级的真实证据，不重试。现有MCP按DOM数量停止，不能保证严格按去重ID首次达标停止。
- 真实DPAPI：PID66012加密并退出，PID58876约335.866秒后解密成功并用原参数请求默认详情，HTTP200，耗时8.281秒，无重新搜索。仅证明该区间有效。共6次详情，已到预算上限；另健康/登录各1次，网络测试停止。
- pytest仅运行`tests/research/test_final_preflight.py`：23 passed，所有SQLite写入强制:memory:，不重跑会创建临时磁盘库的历史全量测试。验证两Project、独立Plan/Run、Post身份共享、证据隔离和4个Dataset版本归属，跨Project错误关联被Trigger拒绝。
- 最小修复：CLI可在显式启动且显式`--new-project 类别`时追加Project，默认仍拒绝；resume读原Plan配置。Run汇总计数/质量/评论/Artifact按Run过滤，全库证据放database_audit并标注范围。没有运行正式启动命令。
- 新增有限验收助手preflight_validation.py、内存验收测试和[最终报告](phase1d_xhs_baseline_collection/preflight_validation_report.md)；复用原Adapter及DPAPI，没有第二套入库Pipeline。Adapter受限评论参数只用于诊断，未静默修改正式Plan。
- 工程保护：4个原DB前后SHA-256完全一致、新增/删除DB为0；正式库0 Run/0 Attempt；研究库完整性/外键通过；12/12脱敏响应哈希通过；项目本轮明文凭据扫描0命中、Git忽略通过。证据在原Phase1D日志目录final_preflight。第三方服务日志未审计，不声明其没有令牌。
- 最终No-Go针对严格完整协议：最新语义未充分确认、去重停止契约及真实超过20能力未完成、正式配置仍需用户明确选择。仅提出最小后续决策，未扩大开发/请求预算；未正式50组、无Commit/Push。

## 2026-10-14 — Phase 1D：正式采集分阶段与恢复增强

- 按用户五项要求新增SQLite搜索完成门槛、原子阶段检查点、请求边界暂停、Windows当前用户DPAPI临时参数缓存、正式独立数据库及显式启动开关。未改18表Schema或Trigger。
- 正式CLI `--start-baseline`仅执行完整50组搜索，结束后PAUSED；详情必须`--formal --resume RUN_ID --stage details`。默认无参数仅帮助。`--formal --inspect`检查阶段；`--formal --pause RUN_ID`请求下一调用前暂停。搜索与详情入口、底层详情请求均检查数据库完成状态。
- 新增`marketlens/research/access_cache.py`、`tests/research/test_baseline_stages.py`和[正式操作说明](phase1d_xhs_baseline_collection/formal_collection.md)；更新baseline_collector、CLI和README。加密缓存按数据库绝对路径/Run/Attempt绑定，先缓存密文再提交搜索响应，重启只读取成功Attempt；损坏/缺失/解密失败停止，不重搜。
- 仅执行`--init-formal`初始化`local_data/phase1d_formal_baseline/research_baseline.db`：18表、0 Run、0 Attempt、integrity_check=ok。未运行真实`--start-baseline`或详情，未新增外部采集请求。
- 完整`python -m pytest -q`：80 passed（本次新增11项）。包含Windows真实DPAPI与新Python进程的synthetic_demo恢复、无明文扫描、阶段暂停/门槛、伪造检查点拒绝、缓存写失败保留证据、CLI显式开关及Pilot隔离。`git diff --check`无空白错误。
- 原Pilot、Phase1C、marketlens.db三库前后哈希均不变；证据在`local_data/phase1d_upgrade_audit/verification.json`。历史Pilot报告未改写，正式库与其隔离。无Commit/Push。
- 限制：缓存不能延长平台临时参数有效期；访问参数过期或未知in-flight Attempt仍停下等待审计，不自动刷新。没有宣称完成正式50组的真实稳定性验证。

## 2026-10-14 — Phase 1D：粉底液基准采集 Pilot

- 按用户授权完成能力检查、批量调度和限定Pilot。新增baseline_config、baseline_collector、phase1d CLI、14项synthetic_demo测试及[报告](phase1d_xhs_baseline_collection/pilot_report.md)。复用Phase1C Adapter、标准化、事务入库和审计，未修改18表Schema/Trigger。
- 固定完整10×5配置；CLI仅允许4组Pilot、最多前5篇详情，完整计划只能打印。串行3秒间隔、90秒客户端超时，首次错误暂停；同Task累计最多2次Attempt。CLI OS锁防止并发写入。
- 本次真实搜索4组、5次调用（1次500失败+4次成功），79条Hit去重为69篇normal；5篇详情成功，64篇未请求，18条图片元数据，50条一级+29条二级。第一次失败后跨进程恢复同一Plan/Run成功，失败证据完整保留。
- 默认评论模式，额外评论请求0。源码确认一级有限滚动配置，但每篇二级10条总预算没有直接接口；本轮未启用load_all_comments=true。没有为凑数重复请求。
- 恢复仅限明确记录的状态：成功Task不重取；已提交响应可修复Task收尾；不确定running Attempt拒绝盲目重放；丢失内存访问参数不偷偷重搜。闭合Run不可重开。当前不声称任意中断可恢复全部旧候选详情。
- 测试：基线55 passed，最终`python -m pytest -q`为69 passed。实际运行`--pilot`后以`--resume 1`有限恢复；最终只读验收integrity_check=ok、外键通过、10/10 Artifact通过；18个保护文件哈希变化0。应用日志/响应/数据库凭据检查通过，local_data被Git忽略。
- 修正Phase1D汇总的未请求口径为无Attempt，避免继承Phase1C无Task口径导致预创建任务后的错误计数；原日志保留，最终审计为64。
- 预计完整默认模式约9.4分钟搜索 + 每候选9.53秒，500—1000候选约1.5—3小时，仅为条件估算。正式采集须用户下一次确认并处理评论覆盖及跨进程访问参数限制。未扩展50组、未冻结Dataset、未Commit/Push。

## 2026-10-14 — Phase 1C：真实图文采集入库

- 用户明确授权Task 1C，按adapters/research/scripts/tests/docs/local_data结构新增隔离实现；不推进Dataset或研究UI。复用现有v0.2.1候选DDL，18表165列不变；旧商品repository保持只读职责。
- 新增标准库HTTP Adapter、保守字段标准化、事务写入、凭据脱敏/Artifact哈希、只读审计和24项离线测试。使用MCP服务v2.5.5的HTTP接口，并非JSON-RPC。
- 实测命令：`python scripts/phase1c_xhs_smoke.py`。一次护手霜/综合/图文搜索保存20条Hit；3次详情保存3个normal快照、13条图片元数据、30条一级评论和18条二级回复；4个采集Attempt均成功。默认评论加载有hasMore，非全量。另17个候选为未请求详情，没有标为失败。
- 检查命令：`python scripts/phase1c_xhs_smoke.py --inspect`。外键通过、integrity_check=ok、4/4Artifact哈希通过；22个保护文件哈希不变。数据库和脱敏响应仅位于忽略的local_data目录，没有鉴权值或访问Token持久化。
- 测试：新增前31 passed；新增离线24 passed；完整回归55 passed。初次离线Git检查受Windows CRLF影响，改为NUL字节输入后通过。未重跑旧Phase1B独立192项脚本。
- 一篇标题为空仍保留；三篇分享量raw为空，数值NULL且标记unparseable。SHA-256对应脱敏重序列化文件，不冒充HTTP原文哈希。
- 新增README与[实验报告](phase1c_xhs_ingestion_smoke/experiment01_report.md)，更新.gitignore。现有用户AGENTS/ingest改动及Phase1B输入/报告保留；无Commit/Push、无Docker/登录/Cookie修改。
- 限制：未构建Dataset、未全量采评论、未下载图片、未测试并发或硬中断恢复。下一步先确认样本范围和未请求候选表达，再实现单Run Draft构建与验收。按用户要求完成后停止。

## 2026-10-09 — Legacy price distribution bug fix

- Scope: only the reported chart error; no consumer MVP gate advancement.
- Reproduced the original `pd.cut` / `groupby` / `st.bar_chart` expression with synthetic prices: Altair raised `SchemaValidationError` because categorical interval objects entered the chart specification.
- Added `marketlens/charts.py` to count known prices and convert intervals into ordered text categories; updated only the price-chart block and its import in `app.py`.
- Empty data/unknown-only prices yield no chart data. Single-record and equal-price inputs retain their counts. Existing empty-database and no-match messages are preserved.
- Added `tests/test_charts.py`: count and label tests plus Streamlit AppTest runs against temporary synthetic databases with 0, 1, and 3 records and no-match searches. The user's database is not written by these tests.
- Installed existing `requirements.txt` dependencies into the project `.venv`; dependency requirements were not changed.
- Validation: `.\.venv\Scripts\python.exe -m pytest -q` — **13 passed in 0.86s**, including the 3 existing ingestion tests. Environment: Python 3.11.4, Streamlit 1.65.0, Pandas 2.3.3, Altair 6.3.0.
- Run: refresh the existing page, or use `.\.venv\Scripts\python.exe -m streamlit run app.py`.
- Limits: headless AppTest verifies chart generation and axis specification; browser appearance was not visually inspected. Other allowed dependency versions were not run. Consumer schema, matching, reviews, and recommendation remain planned.
- No new external data was collected. Stop after this fix for user review.

## 2026-10-10 — Phase 1A: 模块化重构

- 用户限定任务：从旧 `app.py` 拆分查询、搜索与统计；不推进消费者版 schema、FastAPI 或 AI 功能。
- 基线：Git 工作区干净；使用 `D:\PythonEnvs\marketlens\Scripts\python.exe -m pytest -q`，结果 **13 passed in 0.88s**。
- 新增 `marketlens/repository.py`：`load_latest_listings(db_path)` 以 SQLite URI `mode=ro` 读取现有表，显式关闭连接；保留旧 SQL 的最新快照选择及返回列；缺少文件不创建数据库。
- 新增 `marketlens/product_service.py`：`search_products()` 返回带类型注解的 `ProductSearchResult`，提供结果表、条目数、店铺数与价格分布；`has_synthetic_data()` 判断完整数据集的合成标记。服务不访问 SQLite，也不 import Streamlit，直接复用现有 `charts.price_distribution()`。
- 简化 `app.py`：保留输入和展示，调用两个独立模块；保留合成警告在筛选前判断、原搜索口径、指标、图表及空状态。
- 新增 `tests/test_repository.py`、`tests/test_product_service.py`，扩充 `tests/test_charts.py` 的页面指标、有效搜索、无结果警告及缺少数据库回归检查。测试只在临时数据库写入合成样本。
- 首轮新增测试有两项测试自身问题：空格样例实际包含于测试名称；文件重命名断言受到准备样本时其他连接的影响。修正空格样例，移除不隔离的重命名断言，保留数据库字节一致性检查。
- 最终全部测试：`& D:\PythonEnvs\marketlens\Scripts\python.exe -m pytest -q` → **31 passed in 1.27s**。环境：Python 3.11.4、Streamlit 1.65.0、Pandas 2.3.3、pytest 9.1.1。
- 数据保护：现有 `marketlens.db` 重构前后 SHA-256 都为 `A8EBCE4B1F81EA0B16C3659144660CA3359E1412052DE35AB4A602B96F7A8080`。没有改变表结构、原始数据、`ingest.py` 或 `charts.py`。
- README 新增当前接口含义、独立调用示例、指定虚拟环境的启动/测试命令及后续 API 包装边界。
- 运行：`& D:\PythonEnvs\marketlens\Scripts\python.exe -m streamlit run app.py`。
- 限制：保留旧版时间文本排序与店铺名称去重口径；结果对象包含 DataFrame，未来 API 层仍需做 JSON 转换；本轮使用无浏览器 AppTest，没有人工视觉验收。消费者数据结构、评论分析与推荐仍未实现。
- 无新增依赖，无外部数据访问，无 Commit/Push；本阶段完成后等待用户检查。当前无阻塞。


## Phase 1D comments and images final closeout (2026-10-14)

Formal Baseline now sends the accepted finite DOM-20 comment configuration through the existing Adapter; Pilot and Phase1C defaults remain unchanged. Focused offline regression: 15 passed, SQLite in memory only. Two Pilot posts: 10 body images and 2 covers downloaded and decoded successfully (813,820 bytes total). Image downloading is NOT connected to formal collection. Four existing database hashes unchanged; formal Run count remains zero. No formal searches, new MCP requests, new SQLite files, commit or push. See [closeout report](phase1d_xhs_baseline_collection/comments_images_final_report.md). Awaiting user review before any further integration.


## Phase 1D unified storage integration (2026-10-14)

Formal details now commit text/comments before downloading all returned body images through the existing safe transport. Content-addressed files and recoverable Run manifests retain independent Media associations. The existing CLI adds formal-only --resume-media and Run-scoped --export-run (Project ownership required); SQLite remains authoritative and no Dataset is frozen. Formal default stops after search. Full pytest collection in the no-disk audit profile: 106 passed, 40 skipped because those legacy tests require new file SQLite, 0 failed. Four existing database hashes and DDL unchanged; formal database remains at 0 Run. No real MCP/media requests, formal collection, new SQLite files, commit or push. See [storage integration report](phase1d_xhs_baseline_collection/storage_integration_report.md) and [storage layout](phase1d_xhs_baseline_collection/storage_layout.md). Awaiting user review.


## Phase 1D comment target parameter consistency (2026-10-15)

Formal HTTP requests, persisted Plan options and target-aware comment statistics now share BaselineConfig.top_comment_target (default 20; supported 20/25/30). Legacy reached_20_unique keeps its fixed meaning. Saved Run configuration remains authoritative on resume; Pilot and Phase1C behavior is preserved. Related offline regression: 86 passed, 0 failed, 0 skipped; in-memory SQLite and synthetic transport only. Existing DB/DDL hashes unchanged. Read-only audit found pre-existing formal Run 1 (one failed search, 49 pending; no detail attempts); its saved target remains 20. No new formal collection should be started before this Run is reviewed. See [parameter refactor report](phase1d_xhs_baseline_collection/comment_target_refactor_report.md). No collection, commit or push.


## Phase 1D bounded detail retries (2026-10-15)

Added runtime DetailRetryPolicy through the existing formal CLI and BaselineCollector, preserving Run/Plan matching and historical max_attempts=2. Explicit 500/502/503/504 failures wait 180-300 seconds interruptibly, with at most five cumulative detail attempts; completed work, unsafe errors and independent MediaStore recovery retain their existing boundaries. Persisted local retry schedules survive pause/restart; missing access parameters never trigger searches. Final isolated offline regression: 155 passed, 0 failed, 0 skipped, including cross-process budget inheritance. Temporary databases cleaned; four existing DB hashes, DDL and all128 formal data files unchanged. Read-only current-user DPAPI restore covers all464 candidates from50 successful searches. Run1 remains search_complete/PAUSED with0 detail attempts. Preserved prior uncommitted edits; no real requests, commit or push. See [detail retry report](phase1d_xhs_baseline_collection/detail_retry_report.md). Awaiting explicit authorization before any real detail collection.


## Run 1 Attempt 164 timeout review (2026-10-15)

Read-only verification confirmed Run1/Task153/Post108, failed first detail Attempt164: TimeoutError/null after90046ms, no snapshot and no running database Task/Attempt. Saved content remains102 details,1340 top comments,749 replies,370 verified media records. Added narrowly scoped ReviewedTimeoutRecovery, immutable review record archiving the blocked Checkpoint, explicit per-Attempt CLI approval, and optional detail-only120-second runtime HTTP timeout. New Attempts consume approval before network I/O; another timeout blocks, old approval cannot replay, and all prior attempts count. The original client error and Plan remain unchanged. Final six-module isolated regression:186 passed,0 failed,0 skipped;31 new tests, including actual subprocess authorization consumption. No network attempts or writes outside the test root; temporary SQLite cleaned. Four real database hashes, DDL and606 formal file hashes unchanged. Current-user process inspection was permission-limited; Docker timeline is user-supplied evidence, not independently rechecked online. No real requests, recovery, commit or push. See [review](phase1d_xhs_baseline_collection/attempt164_timeout_recovery_report.md). Awaiting explicit execution authorization.


Run1 retry interval pre-resume check (2026-10-15): relaxed only DetailRetryPolicy and saved-wait lower bounds from180 to30; default180-300, cumulative budget5, Plan, normal3-second interval, timeout and safety gates unchanged. One old Attempt62 wait is consumed; no pending wait. Attempt164 evidence/raw/Checkpoint bindings match and no review decision exists. Added10 offline test cases;196 passed,0 failed,0 skipped. Four DBs, DDL,606 formal files and original review evidence hashes unchanged; temporary test databases cleaned, no real requests/resume/commit/push. See [report](phase1d_xhs_baseline_collection/retry_30_60_pre_resume_report.md).


Run1 source-deletion review (2026-10-15): verified failed TimeoutError/null Attempts164/165, no target snapshot, no running Attempt and intact database. Added narrowly scoped explicit local confirmation using existing Task.cancelled and immutable source_deleted audit evidence/time/Checkpoint archive; no Adapter or auth access in confirmation. Collector skips only the reviewed target, reports unavailable separately and closes partial rather than treating deleted source as success. Original candidates, successful tasks and all historical Attempts remain intact. Seven-module isolated offline regression:216 passed,0 failed,0 skipped (20 new tests), including real subprocess recovery and crash-gap repair. Four database hashes, DDL and610 formal file hashes unchanged; temporary SQLite cleaned; no network requests, formal confirmation, recovery, commit or push. See [report](phase1d_xhs_baseline_collection/source_deleted_skip_report.md).


Generic pending-review support (2026-10-15): implemented reusable manual comment_loading_stalled/detail_timeout_unknown classification, Task.partial deferral, immutable per-Attempt review and activation records, and one-use refill authorization through the existing Collector/CLI. Closed Run Triggers require provisional PARTIAL with run_sealed=false while deferred work remains; no reopening or Schema change. Lifetime budget, other safety stops, Source Deleted and successful-task skipping preserved. Verified Attempt249=Run1/Task237/Post192,120016ms TimeoutError/null,0 snapshots and0 comment observations. Eight-module isolated offline regression:238 passed,0 failed,0 skipped;22 new tests including subprocess refill. Four real DBs, DDL and955 formal file hashes unchanged; temporary SQLite cleaned. No real labels, requests, recovery, commit or push. See [report](phase1d_xhs_baseline_collection/pending_review_report.md).







## 2026-10-16 - Formal detail pacing

Added runtime DetailPacingPolicy: existing fixed interval + U(0,10) buffer; U(5,10) cooldown after successful persistence and existing media handling. Persisted deadlines survive pauses, runtime configuration changes and collector reconstruction. Added safe timing/status journal and explicit access-restriction markers. Historical Plan, attempt budget and review binding remain unchanged. Offline regression: 227 passed / 5 skipped / 0 failed. No real network, Run 1 recovery, Commit or Push. See phase1d_xhs_baseline_collection/detail_pacing_report.md.

### Optional batch rest

Extended the same policy and append-only pacing journal with opt-in 15-30 success batches and 60-300s rests. Activation watermark excludes historical successes; persisted unique-task accounting recovers committed successes after a crash. Reused the interruptible deadline loop. New batch tests: 21 passed, including actual subprocess resume with synthetic memory SQLite. Combined offline regression: 248 passed / 5 skipped / 0 failed. Formal directory, existing DB and DDL hashes unchanged (1265 files). Attempt 315 remains BLOCKED and needs its existing explicit review before any authorized recovery. No network, schema/Plan/MCP changes, Commit or Push.

### Authorized formal Run 1 resume

User authorized remaining formal details without a five-task cap. Verified Attempt315 identity, original raw and Checkpoint hashes, server request completion, no other collector or unresolved request. Created bound evidence outside the formal directory; existing CLI archived the original blocked Checkpoint and consumed one-time approval with Attempt316. Background PID55288 started with 120s detail timeout, 30-60s bounded retries, existing single-task pacing and batch rests (first target25). Initial Tasks302-305 returned HTTP200 and committed 27 top comments / 12 replies in total; original Attempt315 unchanged and no new search Attempts. Checkpoint RUNNING at observation; collector continues and retains automatic blocking. No business-code/MCP/Schema/Plan edits, Commit or Push. See phase1d_xhs_baseline_collection/run1_resume315_start_report.md.

### Attempt489 human-reviewed skip and continued collection

After 171 further successful details, Attempt489 / Task473 / Post446 timed out at 120s and blocked. User manually reported the post banned/restricted and authorized marking and skipping it. Reused generic pending_review (detail_timeout_unknown) with an explicit human note; did not misclassify it as source_deleted or successful. Bound evidence: local_data/phase1d_attempt489_review/attempt489_pending_evidence.json. Existing review CLI set Task473 to partial and preserved Attempt489, raw and original Checkpoint; counts became 418 success, 4 pending review, 1 confirmed unavailable, 41 unrequested. Continued previously authorized formal details in background PID64380 with unchanged retry, timeout, pacing and persisted batch settings. No new code, Schema/Plan/MCP changes, Commit or Push.

### User-authorized refill of three accessible deferred posts

User manually confirmed Tasks237/Post192,300/Post255,301/Post256 accessible and authorized refill. Verified exact source IDs, original pending-review/raw evidence, no unresolved requests or other collector, no successful snapshots and remaining lifetime budgets. Existing CLI activated reviews249,312,314 without overwriting originals. Formal process PID46976 used unchanged timeout/retry/pacing/batch policy. Attempts531/532/533 succeeded HTTP200 in 18.016/12.594/12.109s, respectively; lifetime attempt numbers2/2/3. Saved top comments20/3/1 and replies3/2/0. Total successful details462; only Task473 remains pending review and Task153 confirmed unavailable. No new search or code/Schema/Plan/MCP changes, Commit or Push. Collector was finishing existing historical media checks after these successes; final Checkpoint is reported separately when process exits.

Refill process exited normally after final media checks. Final Checkpoint at 2026-10-16 20:10:38 Asia/Shanghai: PARTIAL / PendingReview;run_open_for_explicit_refill. Final counts: 462 successful details, 1 deferred (Task473), 1 confirmed unavailable; 6589 top-comment observations and 3657 reply observations. No new failure or automatic refill of Task473.

### 2026-10-19: local Run finalization implementation, formal execution withheld

Added an explicit offline finalization module and CLI validation/execution routes over the unchanged 18-table Schema. Permanent banned/restricted no-refill decisions are immutable sidecar evidence; source deletion remains distinct. Validation checks candidate/task identities, successful Attempt/snapshot ownership, raw hashes, manual review bindings and clean request state. Intent-first closure anchors the immutable journal SHA-256 in collection_runs.error_summary, closes only the Run in a transaction, then atomically synchronizes Checkpoint. Repeating the same command repairs commit/file gaps; ordinary resume blocks before auth/Adapter/DPAPI and cannot reopen a closed Run. Later Dataset/new Run creation does not invalidate sealed history.

New synthetic offline fault-injection tests and a real Run 1 isolated-copy validation passed; the latter retains 462 successes, 1 source deletion, 1 restricted permanent skip and all 21 historical failed Attempts. Isolation audit recorded zero network/subprocess/forbidden writes; Schema and Trigger definitions unchanged. Evidence and the prepared formal decision file are in ignored local_data/phase1d_local_finalization_validation. Detailed commands, protocol and results: [local_finalization.md](phase1d_xhs_baseline_collection/local_finalization.md). Formal Run/Checkpoint not finalized; no platform requests, directory cleanup, Commit or Push.

Final validation: 30 new finalization tests passed; full `python -m pytest tests -q --tb=short` completed with **384 passed in 42.68s**, no failures/skips. Existing CLI test fixtures now mock the early read-only connection as well as initialize, and stage/comment-target tests explicitly disable unrelated wall-clock pacing. Formal database/directory and external review evidence **2752 files** retained identical SHA-256 and sizes, with no formal path additions/deletions; integrity_check=ok and foreign_key_check empty. Git diff whitespace check passed.

### 2026-10-19: user-authorized formal Run 1 finalization and post-seal backup

All final prerequisites passed: no collector/writer process or unfinished Attempt; OS collector lock and DB write exclusion verified; original pre-seal backup 2756 SHA-256 entries reverified; unchanged real formal decision bound to Run1/Task473/Post446/Attempt489 and original evidence. Executed the validated local CLI under a network/subprocess/DPAPI denial guard. At 11:07:12.731 Asia/Shanghai, formal Run1 became partial and Checkpoint completed/PARTIAL. Inspection and check-finalization confirm sealed=true, checkpoint_synchronized=true, recovery_required=false; 50 searches, 462 detail successes, 1 source deletion, 1 restricted permanent skip, 0 pending review, all 21 historical failed Attempts retained. Task153/473 remain cancelled/partial; no Task/Attempt/raw/comment/snapshot changes or Schema migration.

Only formal DB and logs/checkpoint_1.json changed, with new logs/finalization_1.json (SHA-256 b3b9f001e99cc5fd13cd1ae9e2534cfffa874554d74356d9f9008a4f64652e0d). Database anchor and Checkpoint hash match; integrity_check=ok and foreign_key_check empty. Post-seal independent backup E:/MarketLens_Backups/MarketLens_Phase1D_Run1_POST_FINALIZATION_20261019T110803+0800 has 2753 payload files/204392611 bytes (2759 files/206246163 bytes including manifests/notes), all 2758 manifest entries independently verified; database logical content and finalization anchors match. The pre-seal backup remains intact and passed full revalidation. Formal execution audit and backup verification are in ignored local_data/phase1d_local_finalization_validation/formal_execution_20261019. No platform/Docker/MCP calls, Dataset freeze, file cleanup, Git staging, Commit or Push. Ready for Checkpoint A file-list review.

### 2026-10-20: Dataset Builder implementation and isolated Run 1 validation

Implemented preview, explicit build/freeze and independent verification in the existing Dataset Builder module without changing the 18-table Schema, triggers, collector, CLI or Run Analytics. Authority validation remains read-only and retains original database-path-bound finalization decisions. Simulation writes only a distinct SQLite backup copy with matching source-Run fingerprint. Stable canonical Manifests bind actual members, source row hashes, review evidence, Raw/media hashes, counts and quality, without source text or authentication material. Parent context retains original Observation IDs and records selected stable-parent links. Publication failures roll back; published uncommitted intents can be adopted only with identical semantics and implementation; frozen versions reject missing/corrupt Manifests rather than overwriting them.

Added 21 synthetic offline test cases including publication/commit fault stages, idempotence, conflicting intents, independently checked DB membership with a valid Manifest hash, immutable members, cutoff selection, anonymous/stable parent context, review evidence changes, media corruption, authority/copy mismatch and new Runs observing the same Posts. Full regression: 416 passed in 45.29 seconds. Preview/verify use query-only connections, and tests deny network, subprocess/Docker and DPAPI.

Created a safe SQLite backup at ignored `local_data/dataset_v1_isolation_validation/20261020T192352+0800/research_baseline_isolated.db`. Simulated Dataset ID 1 is frozen: 487 Posts (462 included, 2 detail_unavailable, 23 type_mismatch), 462 selected Snapshots, 10,246 Comments (6,589 top, 3,657 replies), 1,677 media associations. Blank titles20, blank comments86 and all missing metrics retained. Task153 source_deleted and Task473 banned_or_restricted are separately linked to original review/evidence/finalization hashes. Repeated build was idempotent and preserved Manifest bytes; independent verification and SQLite integrity/FK checks passed. Manifest SHA-256: 153f3495b68b3d6d159768af5d38889dd81022decb825ff3b99ca2913bce12cd. Aggregate validation receipt is in the same directory. An earlier attempt at `20261020T192111+0800` stopped before Dataset insertion because the validation script misread an encoded plus sign in a file URI; its unfinished copy is preserved, and the guard was corrected for the successful attempt.

Authoritative Dataset tables remain empty. Formal data tree (2745 files/204381817 bytes, digest 65b0eb8c32523aa9b36945c510380ca198c6ddb3b2c6118ff75b1b72efa2c54b), eight external human-evidence files, and both independent backup content inventories matched before/after. Backup payloads were not modified or decrypted. No platform/MCP/network requests, formal Dataset freeze, Schema migration, directory cleanup, Git staging, Commit or Push. Await user approval before any formal freeze.

### 2026-10-20: read-only Run 1 final reporting after formal Dataset freeze

Verified the now-formally-frozen Dataset ID 1 against its Manifest, members and source evidence using the existing independent verifier, then reused `analyze_run()` without changing its contract. Added `run_report.py` with aggregate collection, a shared JSON-to-Markdown renderer and an explicit offline CLI. Dataset aggregates are queried from frozen members, while Run metrics retain their own observation rules. Small report-specific queries cover Task/Attempt reliability, finite comment reply coverage and available pacing/retry journals; they do not introduce an analytics framework or database tables.

Generated `docs/phase1d_xhs_baseline_collection/run1_final_report.md` and `run1_metrics.json` from the same result object. Core numbers, quality tables, keyword/sort overlap and discovery timeline are computed rather than maintained in documentation. The report records actual implementation hashes separately from Git HEAD, which predates the uncommitted analytics/builder/report code. Timing definitions separate local client duration, recorded waits, planned retry delay and wall-clock spans. Run-scoped invocation counts exclude three saved outputs without Run IDs rather than attributing them by assumption.

Added 10 synthetic offline reporting test cases, including different Run/Dataset observation selections, privacy, project/Run scope rejection, invalid Manifest refusal, protected output directories, incomplete/missing logs, unknown timing and zero denominators. Full regression: 426 passed in 46.90 seconds. Canonical JSON is also the renderer input, so reloading the saved JSON reproduces identical Markdown including table order. Source preservation and generated-report consistency were checked separately. No formal database, Dataset, Manifest, Schema, collector or original evidence changes; no platform/Docker/MCP calls, annotation work, directory cleanup, Git staging, Commit or Push. Statistical results and reproduction command are in the generated report rather than duplicated here.
