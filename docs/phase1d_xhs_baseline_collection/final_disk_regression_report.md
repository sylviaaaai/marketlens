# Phase 1D 最终磁盘回归报告

日期：2026-10-15。**Phase 1D GO：关键磁盘回归通过，可以准备启动正式50组搜索。** 本轮没有启动正式采集、真实MCP、Docker或图片下载，没有Commit/Push。

## 实际执行结果

```powershell
python -m pytest tests/research/test_baseline_collection.py tests/research/test_baseline_stages.py -q -r s
```

最终 **25 collected / 25 passed / 0 failed / 0 skipped，18.56秒**。

| 模块 | 实际执行 | 结果 |
|---|---:|---|
| test_baseline_collection.py | 14 | 全通过 |
| test_baseline_stages.py | 11 | 全通过 |

此前因无磁盘模式跳过的24项全部成功执行；另外一项是此前已通过的默认CLI/只读检查测试，因此本轮两个完整模块合计25项，而不是24项。本轮没有重跑全仓库146项。

## 隔离审计与环境

执行前逐项阅读 conftest、目标测试、SyntheticAdapter、test_smoke中的响应/详情fixture、CLI、Collector、database及DPAPI辅助代码。所有真实业务模块保持原样。

- setup/make 明确把数据库放在 pytest tmp_path；跨进程测试通过命令行把同一临时路径传给新Python进程。
- CLI fixture 替换 DATA、DB、FORMAL_DATA、FORMAL_DB；CLI Adapter、健康/登录检查和鉴权读取均替换为合成实现。用于“保护Pilot”的占位文件也在测试目录，不是真实Pilot。
- 搜索及详情只使用 SyntheticAdapter；合成媒体URL是 example.invalid，下载器在DNS之前按可信域名规则拒绝。
- 用 tempfile.mkdtemp 创建独占目录，pytest basetemp、TEMP/TMP、JUnit及文件输出都位于其中；禁用缓存和pyc写入。
- 仅在子进程环境移除 MARKETLENS_TEST_MEMORY_ONLY，不修改用户/系统环境；临时 sitecustomize 审计钩子由测试及DPAPI子进程继承，拒绝任何目录外数据库连接、文件写入和网络/DNS尝试。仅Windows NUL日志设备作为无文件输出允许使用。
- 使用原SQLite连接、事务、Trigger、Windows DPAPI和原始文件锁实现；没有用内存库替代这些磁盘测试。Schema初始化所需的额外内存校验仍保留。

最终审计记录：58次SQLite连接，其中29次磁盘连接覆盖24个独立临时SQLite文件，其余为内存Schema检查；测试及DPAPI子进程均有防护启动记录。**网络尝试0、越界写入0、越界数据库连接0。**

详细证据保存在被Git忽略的 `local_data/phase1d_final_disk_regression/`：`first/`、`second/`、`final/` 的结果与前后快照，以及 `lock_probe.json`。临时运行器属于验证产物，不是第二套采集入口。

## 问题分类与最小修正

首次防护启动误拦了pytest写入Windows NUL设备，在测试执行前退出；随后运行曾有23通过、2失败。失败分别来自：

1. 临时审计日志把Windows subprocess默认的 executable=None 当作路径，阻止了DPAPI子进程启动；以及运行器的GBK/UTF-8结果整理不匹配。仅修正临时运行器，先恢复日志并清理该轮目录。
2. `test_explicit_cli_start_is_search_only_and_preserves_pilot` 中的 BaselineConfig 测试替身强行添加 interval_seconds=0，恢复时保存的配置已包含此字段，造成重复关键字TypeError。这是历史测试fixture问题。

仅修改 `tests/research/test_baseline_stages.py` 一行测试配置构造：

```python
lambda **kw: BaselineConfig(**{"interval_seconds": 0, **kw})
```

调用方显式提供的配置优先，初次构造仍默认间隔0。没有删除或降低断言，没有改Skip，没有修改业务代码或Schema。修正后两个模块全量重跑，得到上述25通过结果。**未发现需要修复的业务代码Bug。**

## 断言与覆盖边界

| 核验内容 | 本轮依据及结论 |
|---|---|
| 完整50组与显式详情 | full_design、search_barrier与显式CLI测试：合成搜索50组成功后暂停，详情需另一次显式调用；450条Hit、8个唯一Post及评论数量符合断言 |
| 未完成搜索不能进入详情 | failed/missing search与forged checkpoint测试：数据库状态为准，伪造JSON不能放行，直接低层_call也被拒绝 |
| SQLite持久化与跨进程恢复 | reopen_database关闭并重开同一磁盘库，只执行剩余搜索，仍只有原Plan/Run；DPAPI测试由新进程恢复5篇详情、0次重搜，且临时磁盘扫描无合成明文访问参数 |
| 搜索/详情暂停恢复 | 搜索一次后保留49项pending；详情暂停后恢复，不重复成功详情，搜索次数不增加 |
| 成功证据与失败状态 | 原子提交后中断可核对恢复；未知in-flight不盲目重放；429不重试、500总额度不重置；失败详情不改变已成功快照 |
| DPAPI错误边界 | 损坏缓存、错误数据库上下文拒绝刷新；缓存保存失败仍保留响应证据并停止 |
| 评论与Pilot范围 | 30+15、8+3、0+0合成返回均按实际数量保存，不截断；已关闭Run不重复采集，模式不匹配被拒绝 |
| 默认入口与Pilot保护 | 无参数和空库inspect不创建文件、不构造联网Adapter；显式正式入口只搜索，保护临时Pilot占位字节，已有Run拒绝再次新建 |

**单写者锁额外核验**使用现有 single_writer 及两个独立竞争子进程：父进程持锁时第二进程收到BLOCKED；释放后另一个进程成功ACQUIRED。此补充检查通过，不计入pytest的25项，不创建数据库。现有CLI测试本身也通过实际锁的获取/释放，但仅凭其顺序执行不能声称验证并发排他性。

这25项没有单独重新断言同库多Project隔离、HTTP评论目标20请求体、图片文件恢复等全部既有存储契约，本报告不把它们冒称为本轮新增覆盖。图片地址拒绝不会使合成详情测试失败，但这不是图片下载成功测试。没有强制切换WAL，也没有物理断电测试；KeyboardInterrupt/提交窗口恢复不等同于所有硬件故障恢复。

## 数据、工作区与清理

四个既有库SHA-256前后相同：

| 数据库 | SHA-256 |
|---|---|
| marketlens.db | a8ebce4b1f81ea0b16c3659144660ca3359e1412052de35ab4a602b96f7a8080 |
| local_data/phase1c_smoke/research_smoke.db | 0888f5fcb2d2e481db7f564c1d7c851a444e29cb223b9ab172b47ec15986b147 |
| local_data/phase1d_baseline/research_baseline.db | 373c586a36d011edd24161887d56db0c0bfb030b3f875a7baa2b1ebb2a129dde |
| local_data/phase1d_formal_baseline/research_baseline.db | 98deab643546a16dd603cf8cc2f506c35d74a6604ad2b3749de1953d5ee92bcc |

正式库只读检查始终 **0 Run、0 Dataset**，integrity_check=ok、foreign_key_check为空。当前DDL文件SHA-256始终为 `74999edcedb6bfa4771876c6a65aa79e9b36d5df98ef94b73277d315a83e01be`；这是用户已有注释调整后的本轮起始文件，不用旧版本覆盖它。18表DDL及Trigger未改。

成功轮临时根目录：`C:\Users\owner\AppData\Local\Temp\marketlens_phase1d_disk_ikps5vsi`，其pytest子目录中实际建立24个SQLite数据库及一个名为pilot.db的合成占位文件。失败轮使用独立的 `marketlens_phase1d_disk_67_uhfqf`；防护启动轮使用 `marketlens_phase1d_disk_aeufmwz6`；锁补充验证使用 `marketlens_phase1d_disk_lock_xt2asv_y`，均位于同一Windows临时父目录。

**以上四个本轮专用目录均已清理**：先确认绝对父路径、专用前缀及创建标记，待子进程退出/文件句柄释放后，只删除对应目录。临时数据库、日志、DPAPI缓存、临时资源记录及可能的SQLite辅助文件随目录一并清理。项目数据库清单仍为原4个，没有新增长期SQLite。

测试前后 git status 和所有非忽略工作文件哈希已保存；原有5处用户修改及未跟踪评审/学习文件保持原字节。工作区新增差异仅为上述fixture一行修正、本报告，以及Git忽略的验证证据。HEAD未改变，未Reset/Clean/Checkout/Stash、未Commit/Push。

本轮到此结束。**可以准备启动正式50组搜索，实际启动仍等待用户确认；本轮未发起采集。**
