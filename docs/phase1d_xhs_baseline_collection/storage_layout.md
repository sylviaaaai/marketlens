# Phase 1D 统一图文存储

SQLite 是文字及采集关系的权威来源；图片原始字节保存在文件系统；Manifest 是资源状态日志；JSONL 导出是可再生的分析输入。本轮不建立 Dataset Version，不实现 OCR、视觉识别或 AI 分析。

```text
local_data/phase1d_formal_baseline/
├── research_baseline.db              # 唯一正式库，原18表
├── raw/                             # 脱敏接口响应证据
├── logs/                            # 阶段检查点及错误
├── private_access/                  # 已有DPAPI加密访问参数
├── media/
│   ├── images/<sha前2位>/<完整sha>.<实际格式>
│   ├── images/.partial/<随机ID>.part
│   └── manifests/run_<run_id>.jsonl
└── exports/run_<run_id>/
    ├── posts.jsonl
    ├── comments.jsonl
    ├── media.jsonl
    └── export_manifest.json          # 行数、文件哈希、归属、选择规则
```

目录按显式任务按需创建；查看状态或调用 `read_run` 不创建目录。正式目录目前仍只有原空库和已有锁文件。所有本地目录由 Git 忽略。

## 图片与来源

`BaselineConfig.media_directory` 默认 `media`，是相对于数据根目录的安全相对路径，不接受绝对路径、上级跳转或与 raw/logs/private_access/exports 重叠的配置。图片按实际解码格式保存为 jpg/png/webp/gif，内容 SHA-256 决定文件名；不重新压缩或修改字节。

相同 URL 指纹且文件完整时直接复用，不再次请求。不同 URL 是否返回相同字节事前无法可靠判定，因此可能各请求一次；取得字节后按哈希只存一份，不宣称仅凭 URL 能识别所有相同图片。每条 `post_media` 仍保留独立关联及顺序，不因共享文件合并观察记录。

实际主键叫 `media_id`；Manifest/导出使用 `post_media_id` 作为明确别名。归属沿真实关系查询：

`Project → Plan → Run → Task → Attempt → Snapshot → post_media`。

`trg_media_update` 允许 local_path 从 NULL 填入一次，其他证据不变；后续修改被禁止。本实现选择不写 local_path，以 Manifest 支持失败、损坏及恢复状态，不改变数据库记录或 Trigger。

Manifest 每项包含 Run/Post/Snapshot/Media/position、URL SHA-256 指纹、相对文件路径、内容 SHA-256、实际格式、宽高、字节数、下载时间、尝试次数、状态及安全错误码。没有源 URL、Cookie、Token 或访问参数。源 URL 继续留在受保护的原始证据和数据库中，不进入公开导出。

搜索封面继续作为 `search_hits.card_cover_json` 元数据保存；正文文件仅处理 `imageList`，不额外下载封面或拼接所谓原图 URL。

## 时序、状态与恢复

正式默认仅执行搜索；完整50组成功后暂停。只有显式 details 动作才进入详情。每篇先提交 Snapshot、所有返回评论、媒体元数据，并将详情 Task 标记成功，再处理该篇图片，之后才进入下一篇。图片成功与详情成功分别统计，详情的 SUCCESS 不代表全部图片完整。

资源日志状态：`pending → staged → success`，或 `failed / needs_verification`。每次网络调用前落盘保留尝试次数，先写临时文件、验证解码和哈希，再落盘 staged 意图，然后原子重命名，最后写 success。

- 已有成功记录必须同时匹配数据库关联、文件路径、大小、SHA-256、格式和尺寸，才能认为可用。
- staged 的目标文件或临时文件可验证时，恢复流程完成提升/成功记录，不重发网络请求。
- 临时文件没有可靠 staged 关联时不当作成功，不自动删除；待检查或在剩余额度内重新请求。
- success 文件缺失/损坏时标记待核验，不静默覆盖损坏证据，导出 `verified_local=false`、可用路径为空。需人工检查并恢复正确文件；这类情况不自动突破预算重新下载。
- 未知 pending/staged 已耗尽预算时标记待核验；没有“写过一行就永久成功”的假设。
- 每次追加 flush+fsync。只允许修复最后一条未换行记录：备份 torn 尾部并原子恢复完整前缀；中间损坏或关联不匹配明确报错。
- 每个 Media 跨进程总尝试上限默认2，配置只允许1或2；超时、连接中断、5xx 可有限重试，403/404/429、安全拒绝和解码失败不盲目重试。不刷新详情或搜索来取得新 URL。

图片下载沿用原 helper 的 HTTPS、单一可信域名、公网 DNS、固定连接地址、TLS 主机名验证、逐跳重定向检查、15 MiB 单图限制和有限超时。不发送 Cookie、Authorization 或 Referer。旧 HTTP 地址仅升级 scheme 为 HTTPS，不回退 HTTP；不修改原路径。正式流程按逐图上限运行，不把上轮12张/60MiB的小样本验证预算当成整个正式语料的总容量上限。

运行中缓存文件元信息未变的校验结果，避免每篇重新解码所有既存图片。新进程、独立资源恢复和新导出实例重新核验。CLI 的既有 OS 单写者锁同时保护采集、图片恢复和导出；直接调用写入 API 时，调用者也必须保证同一数据根目录只有一个写者。

## 文本读取与导出

`read_run(conn, project_id, run_id, data_dir)` 以 SQLite 只读事务返回 posts/comments/media 三个列表；不调用网络、不写库、不创建文件。公开的输入是结构化 ID，不是任意 SQL。

| 文件 | 权威来源及字段 |
|---|---|
| posts.jsonl | posts 稳定 source_post_id；该 Run 成功详情 Attempt 下的 post_snapshots：snapshot_id、标题、正文、发布时间原值/解析值、观察时间、互动量/精度及 raw_artifact_id；同 Run 的搜索命中引用、关键词和排序 |
| comments.jsonl | 同一 Snapshot 的 Attempt/Post 下 comment_observations：observation_id、comment_id、正文、层级、parent_observation_id、来源父ID、时间、点赞精度、source_path；comments 提供 parent_comment_id；父观察内容限定在同 Post、同 Attempt |
| media.jsonl | SQLite Media 关联 + 已验证 Manifest 状态：位置、verified_local、相对路径、哈希、格式、尺寸、字节数、状态和错误 |

选择规则是**导出指定 Run 的所有成功详情 Snapshot，按 snapshot_id 排序**，并按 observation_id 导出评论。没有跨库“最新快照”查询，没有将未请求详情的候选伪造成正文。重复观察不静默去重，稳定身份与观察身份分别保留。UTF-8 保留原中文、空格、换行、缩写与品牌写法；缺失值用 null，不填造。

`export_run` 在只读事务中取得一致数据库视图，按稳定键排序生成 JSONL，每文件原子替换，最后写完成标记 `export_manifest.json`。消费导出前应核对标记内归属和各文件 SHA-256；中途中断的混合文件不能当作完成。相同数据、资源状态和完整文件重复导出字节一致；导出不是 Dataset 冻结。不能向原始证据目录导出，也不能用同一导出目录覆盖另一个 Run。

```powershell
# 以下仅为使用说明，本轮未对正式库执行这些写文件动作。
python scripts/phase1d_baseline_collect.py --formal --inspect
python scripts/phase1d_baseline_collect.py --formal --resume-media 1 --project-id 1
python scripts/phase1d_baseline_collect.py --formal --export-run 1 --project-id 1
# 可另给 --output-dir 指定派生文件目录；Run/Project ID以实际建立结果为准。
```

独立恢复及导出复用同一 CLI，不初始化库、不做 MCP 预检查、不改 Run/Task 状态，已关闭 Run 也可处理资源。恢复返回码0表示该 Run 所有已记录媒体均验证成功，2表示仍有未完成资源。正式库无 Run 时不能执行示例 ID。

后续文字 AI 可直接使用 `read_run` 的标题、正文、评论及父评论上下文；图片路径和关联只供以后按 ID 获取文件。本轮没有实现 OCR、识别、Embedding、向量库、总结或抽取算法。
