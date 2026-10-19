# Phase 1D 正文与评论有限自动重试验收

日期：2026-10-15。本轮只修改代码、执行离线测试和只读核验。未启动正式 Run 1，未发送真实搜索、详情、登录检查或图片下载请求。

## 结果

正式 `feed_detail` 增加显式启用的运行时 `DetailRetryPolicy`。默认不启用；搜索、Pilot、Phase1C 的原行为不变。Run 1 原 Plan 中 `max_attempts=2`、一级评论目标20及配置一致性校验全部保留，不迁移或覆盖历史 Plan、Task 配置或已完成 Attempt。

仅对已明确收到并记录为 `HTTPError` 的 500/502/503/504，随机等待180–300秒后重试当前笔记。五次预算包含首次请求及历史 Attempt，使用 SQLite 已保存的次数/编号核对，跨进程不会重置。达到预算后阻断整个运行，保留失败笔记，不跳过后继续。

401/403/429、其他未列入的错误、网络结果未知、入库错误和进行中的未知 Attempt 均阻断。5xx 响应若含明确登录/访问验证错误提示，也阻断。重试等待结束后，调用现有 Adapter 的健康和登录预检查，只有 `ready is True` 才重试；不自动登录。预检查不是额外评论或搜索请求。

正文、互动和评论仍由同一个详情响应通过现有事务入库。一级评论不足20不是失败，超过目标也不截断；自然返回的二级回复全部按原关系保存。图片仍在详情提交后走现有 MediaStore；图片失败不会改变详情成功状态，不会触发重抓正文评论。

## 代码与证据

本轮修改/新增：

- `marketlens/research/baseline_config.py`：新增独立运行时策略；不加入 `BaselineConfig.persisted()`。
- `marketlens/research/baseline_collector.py`：有限重试、累计预算、可暂停等待、失败证据核验、缺失访问参数时整体阻断。
- `scripts/phase1d_baseline_collect.py`：新增四个明确 CLI 参数；仅允许正式 Run 的显式 details 恢复启用。
- `tests/research/test_detail_auto_retry.py`：44项合成测试，含真实子进程和隔离临时磁盘测试。
- `docs/phase1d_xhs_baseline_collection/formal_collection.md`、`README.md`、`docs/WORK_LOG.md`：追加最新操作说明与状态。
- 本报告 `detail_retry_report.md`。

运行时证据继续位于被 Git 忽略的现有数据目录，不新增研究数据库：

- SQLite 原有 Attempt 和脱敏 raw：每次实际调用、HTTP状态、错误、返回数量与事务结果。
- `logs/detail_retry_<run>.jsonl`：策略、Attempt信息、等待、预检查是否明确就绪及最终成功/停止原因，追加写入并 fsync；发现尾部不完整时停止。
- `logs/detail_retry_wait_<attempt_id>.json`：每个失败 Attempt 的随机等待秒数、安排时间和最早重试时间；原子保存，重启复用，不重新抽取等待。
- 原有 Checkpoint：增加运行时策略信息；等待时为 WAITING，暂停时为 PAUSED。等待每次最多 sleep 1秒并检查暂停标记；Ctrl+C 同样保留检查点和等待证据。暂停期间经过的时间计入等待期限。

证据缺失、损坏、写入失败均停止，不推测成功或重放未知请求。磁盘完全不可写时只能尽力记录内存错误并停止，不能保证任何程序还能写出检查点。

## 离线验证

最终执行以下六个模块：

```powershell
python -m pytest tests/research/test_baseline_collection.py tests/research/test_baseline_stages.py tests/research/test_detail_auto_retry.py tests/research/test_comments_images_preflight.py tests/research/test_storage_integration.py tests/research/test_final_preflight.py -q -r s
```

实际结果：**155 passed、0 failed、0 skipped，28.00秒**。各模块分别14、11、44、35、28、23项。

通过临时 `sitecustomize` 审计钩子执行：关闭真实网络；禁止连接临时目录外的 SQLite；禁止临时目录外写入；子进程继承同样限制。取消仅测试子进程中的 memory-only 跳过设置，启用真实临时磁盘。测试等待全部由假时钟推进，没有真实180–300秒等待。

新增断言覆盖四种可重试状态恢复、五次预算耗尽、历史两次尝试后新进程只能再发三次、禁重试状态、登录不明确、成功任务跳过、正文评论整体回滚、本地响应保存失败后不确定 Attempt 阻断、等待中暂停/重启、Ctrl+C、等待/日志/raw证据损坏、图片失败独立、短评论成功、阶段门槛、缺失参数不重搜、CLI正确传入策略及历史 Plan 不变。

测试根目录：`C:\Users\owner\AppData\Local\Temp\marketlens_phase1d_disk_eta0tido`，测试结束后已验证归属并完整清理；无新增长期 SQLite。审计记录保存在被忽略的 `local_data/phase1d_detail_retry_validation/retry_final/`。最后一次隔离回归确认数据库清单、工作文件、HEAD和Git状态在测试前后相同，无网络访问尝试或越界访问事件。

## Run 1 与访问参数只读核验

本轮核验：50/50搜索成功，464篇图文候选任务 pending，详情 Attempt 为0；正式库1 Run、0 Dataset，Checkpoint 仍为 `search_complete / PAUSED`。

在正常 Windows 用户上下文中，DPAPI自检通过，50个成功搜索 Attempt 对应的50份缓存全部可解密，恢复464个唯一访问参数条目，覆盖全部464个候选，缺失0。没有显示或导出明文参数；只输出聚合数量。受限代理进程本身无法解密该用户的缓存，因此另外使用同一用户的普通进程进行只读核验并成功；后续采集应使用原 Windows 用户及当前数据库绝对路径。

这证明本地恢复能力和覆盖率，**不证明平台端参数仍有效**。发生缓存缺失或解密失败时阻断；开启自动重试后，在发任何详情前检查全部未成功候选的参数覆盖。没有自动重新搜索的回退路径。平台返回访问限制时同样停止并保留证据。

四个既有数据库 SHA-256 前后相同：

| 数据库 | SHA-256 |
| --- | --- |
| `marketlens.db` | `a8ebce4b1f81ea0b16c3659144660ca3359e1412052de35ab4a602b96f7a8080` |
| Phase1C | `0888f5fcb2d2e481db7f564c1d7c851a444e29cb223b9ab172b47ec15986b147` |
| Pilot | `373c586a36d011edd24161887d56db0c0bfb030b3f875a7baa2b1ebb2a129dde` |
| 正式 Baseline | `e154d85a58cb0c4999843e5fbf6bae77e0861933095137b9986827d3ed9ac152` |

18表DDL文件哈希仍为 `74999edcedb6bfa4771876c6a65aa79e9b36d5df98ef94b73277d315a83e01be`，未修改Schema或Trigger。正式数据目录全部128个文件的清单与哈希保持不变，包括缓存、raw、日志、Checkpoint和数据库。用户其他既有未提交修改均保留，未 Reset/Clean/Stash/Commit/Push。

## 审核后如何恢复（本轮未执行）

在 `E:\Project\marketlens`，使用原 Windows 用户运行。此命令会进行真实详情、评论与原有图片采集，必须等用户明确授权：

```powershell
python scripts/phase1d_baseline_collect.py --formal --resume 1 --stage details --auto-retry --retry-max-attempts 5 --retry-min-seconds 180 --retry-max-seconds 300
```

不会创建新 Run，不修改原 Plan，不重新搜索，成功详情不会重复。既有单写者锁继续生效。命令先做DPAPI可用性和登录预检查，调度器恢复成功搜索的缓存并检查覆盖后才发详情。若参数无法恢复或登录不明确，阻断并检查证据，不自动重搜或重新登录。

另一个终端请求安全暂停：

```powershell
python scripts/phase1d_baseline_collect.py --formal --pause 1
```

等待期间下一秒左右停止；正在进行的HTTP调用按原有边界收尾。再次使用相同详情命令恢复，累计次数和原等待期限继续保留。未知进行中请求、入库错误或预算耗尽应先人工审核，不能靠重启规避。

结论：离线实现与兼容性验证通过，可在用户明确授权后准备恢复 Run 1 详情阶段。本轮已停止，未执行真实采集。
