# Phase 1C — 小红书图文采集与 SQLite Smoke Test

本阶段验证真实搜索 → Adapter → 字段标准化 → 独立 SQLite Collection 的最小链路。使用现有 v0.2.1 candidate DDL，18 张表保持原样。旧商品库、商品页面及 Phase 1B 文件不变。

Experiment 01 已成功：20 条图文 SearchHit、3 个详情快照、13 条图片元数据、30 条一级评论和 18 条二级回复。具体时间、质量缺失和验收结果见 [experiment01_report.md](experiment01_report.md)。这不是完整评论集或 Dataset 完成声明。

## 目录职责

| 路径（项目根目录相对路径） | 用途 |
|---|---|
| `marketlens/adapters/xiaohongshu_mcp.py` | v2.5.5 MCP 服务 HTTP 接口、只读请求白名单、计时与凭据脱敏 |
| `marketlens/research/database.py` | 独立建库、每次连接核验两个 PRAGMA、事务及完整性检查 |
| `marketlens/research/normalizers.py` | ID、互动量 raw/value/precision、可确认的时间转换 |
| `marketlens/research/collection_pipeline.py` | Search/Detail 编排、身份去重、追加观察、审计 |
| `marketlens/research/dataset_builder.py` | 后续阶段占位，本阶段不构建 Dataset |
| `scripts/phase1c_xhs_smoke.py` | 命令行入口 |
| `tests/research/test_smoke.py` | 独立临时库和明确 synthetic_demo 响应；不调用真实服务 |
| `local_data/phase1c_smoke/research_smoke.db` | 唯一默认实验库，禁止提交 |
| `local_data/phase1c_smoke/raw/` | 脱敏响应与传输元数据，每个实际采集 Attempt 一个文件 |
| `local_data/phase1c_smoke/logs/` | 事件日志、每次实验摘要、保护文件指纹与验收证据 |

## 运行

在项目根目录使用已安装 requirements 的 Python，不增加依赖：

```powershell
python -m pytest tests/research -q
python -m pytest -q
python scripts/phase1c_xhs_smoke.py --init-only
python scripts/phase1c_xhs_smoke.py --inspect
```

`--inspect` 只读打开既有实验库并逐表计数、检查外键/完整性、核验文件长度/SHA-256及关系，不发请求。不存在数据库时明确失败。

以下命令会执行一次新的真实实验，默认追加新 Run，绝不清空旧记录：

```powershell
python scripts/phase1c_xhs_smoke.py
```

固定使用本机 `127.0.0.1:18060` 的已有服务。先检查 `/health`、`/api/v1/login/status`；不登录、不修改 Cookie、不变更 Docker。鉴权值优先读取当前进程 `XHS_AUTH_TOKEN` / `AUTH_TOKEN`，否则只读使用兄弟目录 `data-source-lab/xiaohongshu-mcp/.env`。可通过 `--auth-env-file` 指定已有环境文件，勿在命令行参数直接输入密钥。不会打印凭据、保存请求头或请求体。

搜索固定为 `护手霜`、`综合`、`图文`，失败最多重试一次。不可达、401/403/429或入库错误立即停止搜索重试。最多选择前 3 个有访问参数的唯一 normal 图文候选，各请求一次详情；所有可识别的搜索命中均保存。详情若实际为视频，保留脱敏响应和失败 Attempt，拒绝生成图文 Snapshot。详情遇到不可达或401/403/429则停止后续请求。

返回码：`0`=本次 SUCCESS 或只读检查/初始化完成；`2`=真实实验 BLOCKED/PARTIAL。服务正常但结果不足 3 篇有效详情也标记 PARTIAL，不用新请求凑数。

## 数据库与证据规则

- 每次连接在事务外开启并验证 `foreign_keys=ON`、`recursive_triggers=ON`。新库从候选 DDL 创建，已有库须与候选表/索引/Trigger定义匹配；不做隐式迁移。
- 真实请求前提交 running Attempt；响应文件保存后，在一个写事务内写 Artifact、完成 Attempt、写 SearchHit或Snapshot/Media/Comments。解析/约束失败整体回滚该批次，再用另一事务记录同次调用的失败与 Artifact。进程硬中断或文件系统故障可能留下 running Attempt/未引用文件；不自动恢复或删除。
- 同平台 Post ID复用身份；不同实际调用创建新 Attempt/Snapshot。重复处理同一 Attempt 时，脱敏文件内容哈希相同且原文件校验通过才跳过；内容改变拒绝，不使用替换写入。合成测试验证跨 Run历史保留。
- 二级评论指向同响应中的一级 Observation，路径为 `list[i].subComments[j]`。缺失稳定ID只存 Observation，不造身份。默认不把作者字段复制进规范化 Snapshot；脱敏响应仍可能有个人信息和原文，只能保留在忽略目录。
- 互动量保留 raw；`1.2万`、`100+`、`1.2万+`分别为 approximate/lower_bound/approximate_lower_bound。空字符串保留为 unparseable；缺字段为 missing；都不会变为0。
- 本地观察时间固定 UTC毫秒格式。仅转换可辨认的10位秒/13位毫秒时间戳或带时区ISO文本；不确定时保留raw，标准化为NULL。
- 凭据优先保护：`raw/`存的是移除凭据、重新序列化后的响应副本，**不是HTTP逐字节原文**。RawArtifact的SHA-256和长度对应实际保存文件，`protection_status`记录此转换。Token只在内存中供即时详情请求使用；`access_params_json=NULL`，不能依靠已存副本重新认证采集。
- `load_all_comments=false`，不主动滚动、不展开全部回复。此模式不支持精确指定返回条数，保留实际返回的全部一二级评论；`hasMore=true`不代表评论已完整采集。没有单独评论HTTP请求，评论耗时包含于详情中。
- 图片仅保留URL及元数据，不下载图片。

只有显式传入 `--reset` 才归档精确默认路径的旧实验库，再创建新库：

```powershell
python scripts/phase1c_xhs_smoke.py --reset --init-only
```

旧库改名为带UTC时间戳的 `.backup.db`，raw/logs保留，不递归删除目录。存在SQLite侧文件时拒绝重置。普通运行没有重置行为。

## 后续 Dataset

本阶段 Dataset三张表保持空。当前 Run有20篇候选，仅3篇获取详情；其余17篇是未请求，不能误标成详情失败。下一阶段需先决定研究样本范围、未请求候选的表达方式与纳入规则，再实现单Run Draft构建和缺失/版本选择验收。不要直接冻结或把48条已加载评论说成全量。
