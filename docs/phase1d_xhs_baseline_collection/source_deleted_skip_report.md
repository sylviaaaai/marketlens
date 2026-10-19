# Run 1：人工确认来源删除后的单篇详情跳过

日期：2026-10-15。本轮仅代码、离线测试和只读核验；未应用正式确认、未恢复Run、未发送网络请求。

## 只读核验

Run1 / Task153 / Post108仍为failed。Attempt164是第1次详情，90046ms；165是第2次，120000ms。两者均为已结束的TimeoutError/null，目标无成功快照。数据库无running Attempt，integrity_check=ok，foreign_key_check无错误。Run仍为102成功、1失败、361待请求，原始图文候选464篇。

“笔记已删除”是本次用户提供的人工观察，本轮没有访问平台独立验证。准备的 `local_data/phase1d_source_deleted_review/source_deleted_evidence.json` 是非授权证据文件，绑定数据库绝对路径、Run/Task/Post、两次Attempt的原始字段、raw哈希及当前阻断Checkpoint哈希；没有明文访问参数。

现有Schema允许Task状态cancelled，Trigger允许未关闭Run更新Task状态和结束时间，但禁止改变Task身份/config、删除Task或改写完成Attempt。没有原因字段，也没有原生“来源已删除”状态。原来的cancelled处理不足以安全跳过并独立计数，因此本轮扩展现有审核和状态机制。

## 实现

- `baseline_collector.py`：增加显式本地确认及审核校验；仅允许固定Run1/Task153/Post108，理由必须为source_deleted。要求精确两次已结束TimeoutError/null、无成功快照、无进行中请求、搜索完成、原Plan/config匹配及原始raw完整。
- `scripts/phase1d_baseline_collect.py`：新增互斥操作 `--confirm-source-deleted`，要求显式Run、Task、Post、理由和证据。此路由不构造Adapter、不读取鉴权、不进行preflight，也不调用run；复用原数据库连接及单写者锁。
- 新增 `tests/research/test_source_deleted_review.py`，更新现有采集说明、README、WORK_LOG及本报告。未改Adapter、重试/超时策略、Schema或Trigger，无新业务模块或新数据库。

未来明确执行确认命令时，先排他创建 `logs/detail_unavailable_153.json` 并fsync，保存source_deleted、人工确认时间、精确绑定、证据文件哈希/位置，以及原阻断Checkpoint全文；已有同名决定不可覆盖。再用事务把现有Task标记为cancelled，其finished_at为审核时间。**不新建虚假HTTP Attempt，不修改164/165、原搜索命中、Post、raw、Plan或当前Checkpoint。** 同一决定可幂等确认。

若审核文件已保存但Task事务失败，恢复被阻断，必须显式重做同一个确认来修复该间隙，不自动应用跳过。缺失、损坏、变更的证据或孤立cancelled状态均不会放行。实际恢复后原进度Checkpoint可以前进，原阻断证据保留于新增审核记录；更早的审核/Checkpoint归档也不改变。

Collector仅跳过通过精确审核校验的该任务，不要求再恢复它的访问参数、不请求它的详情。其他失败任务仍走原阻断规则；不存在“超时即跳过”。已经成功的102篇仍由原逻辑跳过详情请求，图片保留原MediaStore独立恢复机制。

## 统计与终态

确认后：原候选仍为464，details.succeeded仍为102，details.confirmed_unavailable=1，unrequested=361；顶层另有confirmed_unavailable_count=1。原两条失败Attempt仍存在并统计为失败。历史请求失败次数与候选的当前审核分类是不同口径。

若其余361篇全部成功，最终为463成功+1确认不可获取，Run.status=partial、result=PARTIAL，正常完成命令返回码0。不会将删除笔记计作成功，也不会把整个语料表示成464篇全部取得详情。若余下笔记出现新的登录、访问、超时、未知进行中请求或入库错误，仍立即BLOCKED。

## 离线测试与数据保护

七个模块（原六个回归模块加source_deleted_review）共 **216 passed、0 failed、0 skipped，32.28秒**，本轮新增20项。

验证：确认操作完全无Adapter/鉴权调用、准确身份范围、reason/证据/Checkpoint/raw不匹配拒绝、进行中请求拒绝、第三次Attempt使旧证据失效、原Attempt/Plan/raw/Checkpoint逐项不变、候选总数保留、独立不可获取统计、取消状态本身不能放行、缺失/损坏审核记录阻断、文件先落盘而事务失败后的显式修复、已成功任务不重抓、跨真实子进程恢复跳过、partial终态、其他401/403/429仍阻断。原30–60秒、五次预算、120秒详情专用超时和超时人工审核回归也全部通过。

所有请求/图片/等待均Mock；测试及子进程继承隔离审计钩子，禁止真实网络和临时根目录以外的SQLite连接/写入，无越界或网络访问尝试。临时目录 `C:\Users\owner\AppData\Local\Temp\marketlens_phase1d_disk_y0zgkfw4` 已清理。审计输出在被忽略的 `local_data/phase1d_source_deleted_review/source_deleted_final/`。

四个现有数据库、DDL和正式目录610个文件前后SHA-256完全一致，包含原164/165、raw、审核记录、所有Checkpoint、缓存、媒体及Manifest。没有新增长期SQLite，用户已有未提交修改保留，未Commit/Push。正式确认记录 `detail_unavailable_153.json` 本轮没有创建。

## 两步命令（仅供审核后执行）

第一步：人工明确确认来源删除，只写本地审核及目标Task状态，不联网、不开始采集。该操作需要用户另行授权；不能仅因本报告存在而执行。

```powershell
Set-Location 'E:\Project\marketlens'; python scripts/phase1d_baseline_collect.py --formal --confirm-source-deleted 153 --run-id 1 --post-id 108 --review-reason source_deleted --review-evidence local_data/phase1d_source_deleted_review/source_deleted_evidence.json
```

确认输出应为SOURCE_UNAVAILABLE_CONFIRMED，成功仍102、确认不可获取1、待请求361、候选464。若正式状态或证据已变化，旧绑定会拒绝，须重新只读审核，不能改写历史来匹配。

第二步：另行明确恢复真实采集，保留五次累计预算、30–60秒重试及120秒详情超时。不再带已消费的Attempt164恢复参数，也不授权165重试。

```powershell
Set-Location 'E:\Project\marketlens'; python scripts/phase1d_baseline_collect.py --formal --resume 1 --stage details --auto-retry --retry-max-attempts 5 --retry-min-seconds 30 --retry-max-seconds 60 --detail-timeout-seconds 120
```

本轮没有执行这两条命令。登录和平台访问参数有效性未在线检查；正式恢复时仍需通过现有检查，缺失参数不会自动重新搜索。完成实现后停止，等待用户审核授权。
