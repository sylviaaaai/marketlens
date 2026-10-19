# Run 1 Attempt 164 超时阻断恢复审核

日期：2026-10-15。本轮未恢复 Run 1，未发送任何网络请求，未修改正式研究数据，未 Commit/Push。

## 只读结论

| 项目 | 核验结果 |
| --- | --- |
| 目标 | Run 1 / Task 153 / Post 108 / feed_detail |
| Attempt | 164，是该任务第1次尝试，已 finished、failed |
| 时间 | 2026-10-15 06:51:07.608Z 至 06:52:37.647Z |
| 客户端记录 | TimeoutError，90046ms，http_status=null |
| 目标任务快照 | 0；该任务没有成功详情 Attempt |
| 未决请求 | 数据库中 running Task/Attempt 均为0 |
| 搜索 | 50/50成功 |
| 图文任务 | 102成功、1失败、361待请求，共464 |
| 已保存内容 | 102快照、1340一级评论、749二级回复 |
| 媒体 | 370条记录全部 verified，unfinished=0 |
| 完整性 | integrity_check=ok，foreign_key_check无错误 |
| Checkpoint | details / BLOCKED / UnsafeFailureNeedsReview;no_automatic_retry |

原始脱敏 raw SHA-256 校验通过。正式 Plan 仍保存 max_attempts=2、top_comment_target=20及对应有限滚动配置；运行时自动重试预算为5。

进程命令行检查因 Windows 权限限制无法完成。数据库没有 running 记录不能单独证明 MCP 内部请求已经结束，因此恢复命令要求人工明确确认服务端旧请求已结束。没有为解决权限限制而启动、停止任何采集进程。

## 阻断原因与90秒边界

当前 CLI 构造 Adapter 时显式设置基础 HTTP timeout=90。Adapter 在未收到响应状态前超时，只能真实记录 TimeoutError/null，不能依据服务端之后的日志将其补写为HTTP500。现有两层安全门槛都拒绝该状态；原程序没有人工审核恢复入口，重复原自动重试命令仍会阻断。

用户提供的同期 Docker 日志摘要说明评论加载缓慢，客户端超时附近出现 context canceled，最终服务端HTTP500、约90秒结束。本轮没有访问在线 Docker 日志，因此这部分保留为**用户提供的证据**，没有声称已独立验证日志或精确定位服务端请求。客户端90秒断开引起服务端取消，与双方超时边界接近，都是合理解释；仅凭该摘要不能确定服务端自身存在固定90秒截止，也不能把两边状态混为一谈。

建议本次显式使用详情专用120秒超时，为约90秒结束的请求留出约30秒接收余量。这不会延长MCP自身的浏览器/内部截止时间，也不保证成功。urllib的timeout用于阻塞I/O，不是整个采集任务严格120秒的总时限。若仍未收到响应而超时，仍按未知传输结果停止。

## 最小修改

沿用现有 Collector、Adapter、CLI 和重试日志，无新Pipeline、数据库、Schema、Trigger或研究Plan变更。

- `baseline_config.py`：新增独立运行时 `ReviewedTimeoutRecovery`，不加入持久化研究配置。
- `baseline_collector.py`：只对明确审核的一个失败TimeoutError/null允许下一次详情Attempt。绑定数据库绝对路径、Run/Task/Post/Attempt、原次数、结束时间、原始raw哈希与阻断Checkpoint哈希；目标必须仍是最新失败、没有成功快照、没有未决请求且预算未耗尽。
- `xiaohongshu_mcp.py`：增加可选详情专用超时；健康、登录、搜索继续使用基础超时。未配置时沿用原行为。
- 现有CLI：新增显式审核参数与 `--detail-timeout-seconds 120`，仅允许正式详情恢复使用；原搜索、Pilot、Phase1C行为不变。
- 扩展 `test_detail_auto_retry.py`；更新正式采集说明、README、WORK_LOG和本报告。

### 一次性授权与审计

本轮仅准备了**非授权证据文件**：`local_data/phase1d_attempt164_review/attempt164_evidence.json`。里面没有访问参数、Token、Cookie或完整认证头；Docker部分明确标注为用户提供摘要。该文件本身不能触发采集。

用户审核并实际执行命令时，运行时入口才会建立 `logs/detail_review_164.json`：排他创建、flush/fsync，保留理由、证据文件哈希/位置、精确任务绑定、允许的下次Attempt编号，以及**原阻断Checkpoint的完整内容**。同名记录不覆盖；恢复等待时只接受相同决定。随后正常进度Checkpoint可以前进，原阻断证据仍完整保存在不可覆盖的审核记录中。

新Attempt在网络I/O前先提交到原有SQLite，并追加 `review_consumed` 事件关联旧164与新Attempt。出现新Attempt后，旧审核入口即失效；崩溃留下running Attempt也会阻断。等待中暂停、尚未产生新Attempt时，可使用同一审核命令恢复原等待。移除审核选项后，不会因同进程缓存而继续保留放行。

该机制只放行审核目标的下一次尝试，不把其他超时纳入自动重试。若新请求再次TimeoutError/null，立即阻断，需要新的审核；若明确收到500/502/503/504，可沿用原有限自动重试。原164永远保持TimeoutError/null。目标现有1次计入累计5次预算，因此剩余最多4次，并不保证会全部发出。

已成功102篇按原入口跳过详情请求；媒体仍走现有独立恢复和验证机制，图片失败不会重抓正文评论。访问参数仍从原DPAPI缓存恢复，缺失即阻断，没有自动重搜。本轮未重新解密全部缓存或验证平台端有效期，不能沿用旧检查断言它们此刻未过期。

## 离线测试

最终执行原有六模块隔离回归：

```powershell
python -m pytest tests/research/test_baseline_collection.py tests/research/test_baseline_stages.py tests/research/test_detail_auto_retry.py tests/research/test_comments_images_preflight.py tests/research/test_storage_integration.py tests/research/test_final_preflight.py -q -r s
```

**186 passed，0 failed，0 skipped，30.81秒。** 相对上轮155项新增31项。重试测试模块共75项，其余14/11/35/28/23项。

新增覆盖：一次性放行、原Attempt和Plan不变、原Checkpoint归档、8种绑定不匹配拒绝、401/403/429/URLError/HTTP500不可冒充目标超时、再次超时阻断、旧授权不可复用、预算不重置、等待暂停恢复、撤销本次显式选项、证据变化阻断、成功任务跳过、真实子进程消费授权后不能重放、CLI参数正确传递，以及120秒仅用于详情请求。

测试以Mock Adapter、Mock HTTP opener和假时钟运行；无真实长等待。独立sitecustomize审计钩子在测试及子进程禁止网络、禁止临时根目录外SQLite连接与写入；没有越界或网络访问尝试。临时目录 `C:\Users\owner\AppData\Local\Temp\marketlens_phase1d_disk_8tq4_1e8` 已清理。测试日志/JUnit/审计均在被Git忽略的 `local_data/phase1d_attempt164_review/reviewed_verified/`，不是研究数据。

四个既有数据库、18表DDL、正式目录全部606个文件前后哈希一致，包含原Checkpoint、全部缓存、raw、媒体和Manifest。HEAD不变，用户其他未提交修改保留。核验见本地忽略目录 `local_data/phase1d_attempt164_review/verification.json`。

## 审核后命令（本轮未执行）

先确认服务端旧请求已结束、没有其他采集进程；使用原Windows用户、原数据库路径。下列命令中的明确确认表示操作者审核后的决定；本报告不推断历史操作者身份。理由可保持如下文字，不能填写任何凭据。

```powershell
Set-Location E:\Project\marketlens
python scripts/phase1d_baseline_collect.py --formal --resume 1 --stage details --auto-retry --retry-max-attempts 5 --retry-min-seconds 180 --retry-max-seconds 300 --detail-timeout-seconds 120 --approve-timeout-attempt 164 --review-reason "Reviewed Attempt 164 evidence; confirmed old server request finished; authorize one further detail attempt without reclassifying the timeout" --review-evidence local_data/phase1d_attempt164_review/attempt164_evidence.json --confirm-server-request-finished
```

该命令会实际恢复失败笔记，成功后继续后续候选；不是单篇探测命令。需要用户另行明确授权后才能执行。审核和缓存校验、健康/登录预检查、单写者锁仍生效；登录不明确不放行。首次审核重试仍使用可暂停的180–300秒等待。

另一个终端请求暂停：

```powershell
python scripts/phase1d_baseline_collect.py --formal --pause 1
```

若暂停发生在授权消费前，可用同一完整审核命令恢复。消费后普通暂停恢复应**移除四个审核选项**，继续保留120秒与自动重试参数：

```powershell
python scripts/phase1d_baseline_collect.py --formal --resume 1 --stage details --auto-retry --retry-max-attempts 5 --retry-min-seconds 180 --retry-max-seconds 300 --detail-timeout-seconds 120
```

若届时出现新的超时、未知进行中Attempt或入库错误，普通命令仍阻断，不自动再次放行。若审核前Run/Attempt/Checkpoint证据已经变化，旧证据文件会校验失败，需要重新只读核验，而不是修改历史记录来匹配。

结论：受限于当前证据，适合采用明确人工确认后的单次恢复；不适合把所有TimeoutError自动视为500。本轮实现和离线测试完成，已停止，等待用户审核；没有创建正式授权记录或执行任何真实恢复。
