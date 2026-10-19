# 通用异常笔记人工暂缓与显式补采

日期：2026-10-15。本轮仅实现、离线测试和只读核验。未标注Attempt249，未恢复Run1，未发送网络请求，未Commit/Push。

## Attempt249真实身份与证据

只读数据库核验为 **Run1 / Task237 / Post192 / Attempt249 / attempt_no=1**。Task与Attempt均failed；TimeoutError/null，120016ms；开始于2026-10-15T08:55:28.526Z，结束于08:57:28.536Z。目标仅有这一次历史失败，没有成功快照，Attempt249的comment_observations为0。MCP页面上曾出现的10条评论不是已保存数据。

原始raw哈希校验通过，没有running Task/Attempt，没有目标既有审核文件；integrity_check=ok，foreign_key_check无错误。当前464篇图文候选为185成功、1篇已确认删除、1失败、277尚未请求。用户报告笔记可访问、评论从0到10后停滞，本轮没有在线重新验证该观察，不根据TimeoutError自行推断分类。

已准备非授权证据 `local_data/phase1d_pending_review_validation/attempt249_evidence.json`，绑定真实身份、历史Attempt字段、原始raw哈希、数据库绝对路径及当前Checkpoint哈希。它不是正式标注或采集授权。

## Trigger约束与选型

Task允许partial状态，未封口Run可变更Task状态；Task身份/config和完成Attempt不可改写。`trg_run_no_reopen`禁止修改已关闭的partial/completed/failed/cancelled Run，`trg_task_update`也禁止修改关闭Run中的任务。因此不能先封口为SQLite partial，再声称可以重新激活原任务。

本次使用 **Task.status=partial + 不可覆盖的pending_review审计文件** 表示暂缓，绝不使用取消状态代表暂缓。有待补采项且其余已处理时，返回result=PARTIAL、phase.stage=pending_review、pending_review_count>0、run_sealed=false；**SQLite Run保持running、finished_at为空**。这是明确的未封口PARTIAL报告，不是偷偷重新打开已封口Run。这样才能不改Schema/Trigger并补采同一Run的原任务。

全部暂缓项显式处理完成后，Collector才允许真正封口；如仍有source_deleted，则按既有机制封口为partial。已经封口的旧Run不会被该功能重新打开。

## 行为与审计

- 通用人工分类支持comment_loading_stalled、detail_timeout_unknown，由操作者明确选择并填写说明；source_deleted继续走独立机制，不混用分类或统计。
- 当前人工暂缓入口仅接受已明确结束的详情TimeoutError/null、最新失败Attempt及无成功快照的任务。没有任何见到超时即自动跳过的规则；401/403/429、进行中请求、其他传输/入库错误仍不能通过此入口放行。
- `logs/pending_review_<task>_<attempt>.json`保存分类、人工说明、审核时间、Run/Task/Post/Attempt绑定、截至该Attempt的完整失败历史、raw哈希、证据路径/哈希和原Checkpoint全文。排他创建并fsync，同一决定可幂等重做，冲突决定拒绝覆盖。
- 文件先保存，原Task状态在事务中更新；崩溃产生文件/数据库不一致时阻断，必须显式重做对应操作修复，不能盲目自动跳过。
- 恢复Collector后只跳过有完整有效审核的暂缓任务；不新增HTTP Attempt或快照，不要求该暂缓笔记的访问参数，不改变既有成功详情。其他候选仍使用原DPAPI缓存与MediaStore；缺失参数不会重新搜索。
- 显式激活创建独立的 `pending_review_<task>_<attempt>_activation.json`，保存授权说明、时间和原审核文件哈希；Task恢复为failed以等待补采，不重置次数。激活本身不联网；下一次details恢复才发请求。
- 激活只允许原失败Attempt之后的一次新尝试。新Attempt在I/O前提交并记录pending_review_activation_consumed；进程重启不能重放未知请求。再次超时需要新的人工审核；若明确收到可重试5xx，继续原有限重试策略。历史累计达到5次时，激活会拒绝，不能借分类/激活重置预算。
- 同一任务后续新的失败可以建立新的独立审核周期，旧审核与激活文件全部保留。实现没有硬编码249/237/192，可用于其他经人工审核的异常笔记；旧source_deleted的单目标约束保持不变。

### 统计口径

| 含义 | 输出字段 |
| --- | --- |
| 原始图文候选 | normal_unique_posts，仍为464 |
| 成功详情 | details.succeeded |
| 已确认不可获取 | confirmed_unavailable_count / details.confirmed_unavailable |
| 暂缓待补采 | pending_review_count / details.pending_review |
| 尚未请求 | details.unrequested |

若未来只暂缓本次目标，当前状态将变为185成功、1确认不可获取、1待补采、277尚未请求，合计464。激活后待补采数减少，该项进入已授权但尚未成功的失败任务口径，直到新请求真实成功才计成功。原失败Attempt计数从不删除。

## 文件与测试

实际生产代码仅修改 `marketlens/research/baseline_collector.py`、`scripts/phase1d_baseline_collect.py`；新增 `tests/research/test_pending_review.py` 和本报告，追加正式采集说明、README及WORK_LOG。没有新业务模块、数据库、Schema变更、MCP/Go代码改动、Docker重建或评论协议变化。

最终八模块隔离回归：**238 passed、0 failed、0 skipped，36.36秒**。本轮新增22项测试，覆盖两类人工分类、身份/证据错误、未审核禁止跳过、历史Attempt/Checkpoint不变、重复与冲突审核、真实子进程恢复与补采、一次性激活、再次超时阻断、累计五次上限、未封口PARTIAL、已删除/暂缓分别统计、文件/事务崩溃间隙显式修复、两个本地CLI操作不读鉴权或构造Adapter。原Source Deleted、30–60秒重试、120秒详情专用超时、评论和媒体测试继续通过。

测试与子进程继承审计钩子，禁止真实网络和临时根目录外SQLite连接/写入；无越界或网络尝试。临时根目录 `C:\Users\owner\AppData\Local\Temp\marketlens_phase1d_disk_cxz1dmm7` 已清理。输出保存在被忽略的 `local_data/phase1d_pending_review_validation/pending_first/`。

四个既有数据库、18表DDL、正式目录955个文件前后哈希相同，包括历史Plan、164/165/249、raw、旧审核与Checkpoint、缓存、媒体和Manifest。用户已有未提交修改保留，无新增长期SQLite，未Commit/Push。正式目录未创建任何pending_review记录，Run1仍保持原BLOCKED状态。

## 两条当前命令（仅提供，未执行）

人工审核并暂缓249，明确采用用户观察的comment_loading_stalled分类。该操作只修改本地审核与原Task，不联网、不开始采集，需要用户另行授权：

```powershell
Set-Location 'E:\Project\marketlens'; python scripts/phase1d_baseline_collect.py --formal --defer-detail-attempt 249 --run-id 1 --task-id 237 --post-id 192 --review-classification comment_loading_stalled --review-note "User confirmed source accessible; comments stalled; defer for later refill" --review-evidence local_data/phase1d_pending_review_validation/attempt249_evidence.json
```

恢复Run1其他候选，保留累计5次、30–60秒、详情120秒及原Plan的一级评论目标20；同样需要另行授权真实采集：

```powershell
Set-Location 'E:\Project\marketlens'; python scripts/phase1d_baseline_collect.py --formal --resume 1 --stage details --auto-retry --retry-max-attempts 5 --retry-min-seconds 30 --retry-max-seconds 60 --detail-timeout-seconds 120
```

## 未来补采

明确确认旧请求已经结束，授权对同一原任务再尝试一次；该命令本身不联网，也不会清空历史：

```powershell
Set-Location 'E:\Project\marketlens'; python scripts/phase1d_baseline_collect.py --formal --reactivate-detail-attempt 249 --run-id 1 --task-id 237 --post-id 192 --review-note "Authorize one refill of the original task after review; retain prior attempts" --confirm-server-request-finished
```

随后再次执行上面的details恢复命令。已成功任务和其他仍暂缓的任务不会重抓；若再次超时，旧授权不会再次放行。若Run/Attempt或证据已经变化，旧操作会拒绝，需要重新核验而非篡改历史匹配。

未来其他笔记复用同一CLI，只替换真实Run/Task/Post/Attempt、人工分类/说明及经只读核验的证据文件；证据binding可由现有Collector的 `_pending_binding` 只读生成并附当前Checkpoint SHA-256。本轮不提供自动在线分类或批量补采调度器。

本轮已停止，未对Attempt249进行实际标注或补采。
