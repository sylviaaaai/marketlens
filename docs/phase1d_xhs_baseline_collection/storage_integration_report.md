# Phase 1D 存储框架最终集成报告

日期：2026-10-14。**代码与离线集成完成，未启动正式搜索、详情或真实下载。** 用户已选择保存全部可获取正文图，首版 AI 只处理文字；本轮仅交付存储与导出基础接口。使用说明见 [storage_layout.md](storage_layout.md)。

## 1. 开始前审计

已核对 AGENTS、产品/架构/实施/数据源文档、BaselineConfig/Collector、CollectionPipeline、Normalizer、Adapter、database、18表DDL和媒体 Trigger，以及已有下载 helper、Pilot/Final Preflight/图片报告、正式说明及 Dataset 占位模块和测试。

| 现状 | 处理 |
|---|---|
| 已有 Adapter、详情响应原子入库、评论完整保存、阶段门槛、DPAPI、单写者 CLI | 复用，没有第二套采集 Pipeline 或连接层 |
| 已有 image_preflight 的可信 HTTPS 下载、解码和大小校验 | 直接复用 fetch/decode，仅补完整响应体及超大像素异常处理 |
| post_media 记录正文 URL、预览 URL、位置和原始元数据 | 复用。实际主键 media_id，导出名 post_media_id；不迁移 |
| local_path 允许仅一次 NULL→非空更新，随后不可改 | 选择不写该字段，Manifest 记录资源生命周期；Trigger 原样保留 |
| 尚缺持久化资源关联、去重、恢复与按Run导出 | 新增 media_store、run_export 两个模块 |
| Dataset builder 仅为占位，历史测试涵盖部分Schema约束 | 不启用、不创建或冻结真实 Dataset |

## 2. 改动文件

- `marketlens/research/media_store.py`：按内容哈希保存原始图片、持久日志、关联核验、有限重试及独立恢复。
- `marketlens/research/run_export.py`：Project归属检查、Run范围读取、可复现三份JSONL及完成清单。
- `marketlens/research/baseline_config.py`：安全相对媒体目录、默认2次总尝试、0.5秒间隔；Pilot持久化配置保持历史格式。
- `marketlens/research/baseline_collector.py`：详情提交后及时下载，媒体失败与详情隔离，资源统计；正式默认动作仅搜索，详情必须显式启动。
- `marketlens/research/image_preflight.py`：复用的解码与响应体完整性检查；没有第二个HTTP下载实现。
- `scripts/phase1d_baseline_collect.py`：同入口新增 `--resume-media` / `--export-run` 与 Project核对；只读检查展示资源状态。
- `requirements.txt`：明确已有图片解码所需 Pillow 依赖。
- `tests/research/test_storage_integration.py`：28项参数化离线集成测试。
- `tests/research/test_baseline_collection.py`、`test_comments_images_preflight.py`：更新正式默认先搜索再显式详情的断言。
- `tests/conftest.py`：可选无磁盘SQLite/禁止真实联网的测试保护模式，常规模式不变。
- 本目录两份存储文档、formal_collection、根README及WORK_LOG：状态与操作边界更新。

原18表DDL、Trigger、第三方MCP、历史Pilot记录和原始证据均未修改；没有新的正式数据库或按Project拆库。

## 3. 图片与文本实现结果

正式详情的顺序是：全部50组搜索成功 → 自动暂停 → 显式details → 一篇详情/评论入库 → 标记该详情Task成功 → 下载其全部imageList资源 → 下一篇。图片失败不会回滚 Snapshot/Comment，也不会将成功详情改成失败；资源汇总单独显示 verified/unfinished/各状态及 I/O 错误。

文件以原始内容SHA-256命名，字节不修改，格式由实际解码结果决定。相同URL且缓存完整不重复请求；不同URL必须先取回字节才能判定是否相同，但同哈希只存一份文件。每个Post/Snapshot/Media/Run仍保留自己的关联。封面维持原搜索元数据，本轮不接入封面下载。

SQLite始终是文本权威源。按Project核对Run，沿真实Task/Attempt定位该Run全部成功Snapshot及其评论。导出保存观察身份、父子关系和同Run搜索命中引用，不取全库最新Snapshot，不输出未请求详情候选的伪正文。中文、缩写、空格和换行保留；缺失信息为null。不存在每篇TXT或第二份文本库。

Manifest/导出只含URL安全指纹，不复制URL、Cookie或访问Token。资源错误仅记录安全错误码/异常类型和HTTP状态；原始响应仍由已有脱敏层处理。文件与数据库非原子性通过 staged 意图和文件校验处理，不靠数据库事务回滚文件。

## 4. 测试与离线样例

完整命令（无测试路径筛选）：

```powershell
$env:MARKETLENS_TEST_MEMORY_ONLY='1'
python -m pytest -q -r s --junitxml=local_data/phase1d_storage_validation/pytest_results.xml
```

最终结果：**146项收集，106 passed、40 skipped、0 failed、0 error，约9.49秒**。这不是“全部146项通过”。40项历史测试依赖新建磁盘SQLite，按本轮禁止新增数据库约束跳过；精确名称和原因见 `local_data/phase1d_storage_validation/verification.json` 与 JUnit XML。

| 跳过模块 | 数量 | 未在本轮重验的主要范围 |
|---|---:|---|
| test_baseline_collection | 14 | 原磁盘fixture；部分核心契约已有内存版重用 |
| test_baseline_stages | 10 | 磁盘阶段恢复、DPAPI子进程、显式CLI建库流程 |
| test_smoke | 9 | 磁盘连接/重开、Schema拒绝、Phase1C磁盘链路 |
| test_charts | 3 | 使用磁盘商品库的图表 |
| test_ingest | 1 | CSV写磁盘商品库 |
| test_repository | 3 | 磁盘商品库读取 |

测试保护模式拦截非内存sqlite3连接，并禁止真实网络；Windows asyncio内部socketpair仅允许作为本地IPC。首次全量运行因该IPC被误拦出现1个测试失败，修正保护工具后全量重跑得到上述最终结果；没有修改应用图表代码或放开MCP连接。

新存储测试与已有评论/下载安全测试共同覆盖：

- 多张正文、内容哈希路径、同URL不再下载、不同URL同字节只存一份、不同Post/Run引用共享文件。
- 准确的Manifest身份和位置；50组完成门槛；搜索阶段无下载；第一篇图片处理后才请求下一篇详情。
- HTTP403/404/429/500、文件系统错误不回滚文字与评论；有限重试次数跨恢复保持。
- 下载中断不假成功；临时文件未关联不冒用；staged在提升前或成功日志前中断均可恢复；成功日志I/O失败可恢复。
- 已成功文件缺失/损坏被检出；关联不匹配拒绝；JSONL撕裂尾部保留备份并修复。
- 两个Project共享Post但Snapshot和评论不串Run；父回复上下文正确；未请求详情不伪造；重复导出字节一致。
- 原文中文/换行/空格、发布时间与互动指标；正式HTTP评论目标20且30+15全部存储；Pilot默认评论行为保留。
- 媒体恢复/导出CLI仅只读连接，不实例化MCP或初始化数据库；原local_path不可变规则仍有效；没有创建Dataset。

持久化的**纯合成样例**位于 `local_data/phase1d_storage_validation/synthetic_demo/`。SQLite只在内存创建并已关闭，没有保存数据库文件。两个Project、两个Run，共用两个稳定Post，产生4个Snapshot、180条评论Observation、12条Media引用；磁盘只有1个12×9 PNG。下载fixture调用数量为第一Run 2次、第二Run 0次（真实网络0次）。

可查看：

- `synthetic_demo/media/manifests/run_1.jsonl` 与 `run_2.jsonl`：状态转换、独立身份和共享内容文件。
- `synthetic_demo/exports/run_1/` 与 `run_2/`：posts/comments/media及完成标记。
- `sample_summary.json`：样例计数；`verification.json`：数据库哈希、完整性、测试跳过清单和安全检查。

例如导出中的媒体记录以 `run_id=1, post_id=1, snapshot_id=1, post_media_id=1, position=1` 关联文字，`verified_local=true`，相对路径在 `media/images/<前2位>/<完整sha>.png`。第二Run引用同一文件，但其Run/Snapshot/Media不同，不复制文字观察。以上是合成接口验收，不是真实平台结果。

## 5. 数据库与文件保护证据

四个既有数据库文件清单前后一致，SHA-256逐字节一致；各库 `integrity_check=ok`、`foreign_key_check=[]`。三个研究库仍为原18表。正式库 **0 Run、0 Dataset**；Phase1C和Pilot各保留原1个Run、0 Dataset。DDL文件SHA-256也未变。

| 文件 | 前后相同SHA-256 |
|---|---|
| marketlens.db | a8ebce4b1f81ea0b16c3659144660ca3359e1412052de35ab4a602b96f7a8080 |
| phase1c_smoke/research_smoke.db | 0888f5fcb2d2e481db7f564c1d7c851a444e29cb223b9ab172b47ec15986b147 |
| phase1d_baseline/research_baseline.db | 373c586a36d011edd24161887d56db0c0bfb030b3f875a7baa2b1ebb2a129dde |
| phase1d_formal_baseline/research_baseline.db | 98deab643546a16dd603cf8cc2f506c35d74a6604ad2b3749de1953d5ee92bcc |

正式目录没有提前生成media或exports。样例的20个文件全部通过Git忽略检查；新Manifest/导出扫描未发现URL、合成鉴权值、Cookie/Authorization或签名参数明文，导出文件哈希核验通过。没有新增磁盘SQLite，没有真实MCP/媒体请求，没有Commit/Push。

## 6. 边界与启动判断

图片可独立恢复，但不是无限重试：未知结果也占用已保留的尝试额度；默认每个Media最多2次。403/429、安全拒绝、解码失败、已成功文件缺失/损坏、日志中段损坏等保留证据等待检查，不绕过限制，不自动重抓详情刷新URL。损坏文件不被静默覆盖；恢复正确文件后可重新核验。磁盘写满时可能连错误日志都无法落盘，仍通过本次返回结果暴露I/O异常。

本轮未验证真实长时间运行、CDN限速/地址寿命、新域名兼容性、大规模磁盘容量、断电后物理存储耐久性和大量Manifest的性能。当前仅允许此前实际验证的媒体域名；其他域名会被拒绝并记录。15MiB单图上限可能使极大正文图保持未完成，不能承诺所有平台图片永远可取。

**工程上的正式搜索入口已就绪；是否启动仍等待用户审核。** 本轮未启动50组或正式详情。用户批准后仍须显式使用已有 `--start-baseline`，搜索完成自动暂停；详情另外显式执行。图片存储已接入正式details，首版AI仍未开发，仅提供文字读取接口。
