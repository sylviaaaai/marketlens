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
