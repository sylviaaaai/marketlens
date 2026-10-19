# Phase 1D 一级评论目标统一报告

日期：2026-10-15。默认目标仍为20；正式模式允许20、25、30。本轮仅参数重构和离线回归，未启动真实采集、未Commit/Push。

## 统一路径

`BaselineConfig.top_comment_target → detail_options.comment_target → 既有Adapter → HTTP comment_config.max_comment_items`。

同一字段还生成 Plan 的 `comment_config.max_comment_items`，并传给 `comment_stats(..., target_unique=self.config.top_comment_target)`。实际返回的一级、二级评论保存逻辑未修改，不按目标截断，不为补数增加请求。

HTTP参数仍是服务端DOM数量的有限滚动目标；唯一ID达标字段只做事后质量统计，不承诺在去重ID首次达到目标时精确停止。

| 文件 | 改动 |
|---|---|
| baseline_config.py | 正式模式提前校验精确整数20/25/30；detail_options及persisted均读取top_comment_target |
| baseline_collector.py | 正式响应统计显式传入当前配置目标 |
| normalizers.py | 新增target_unique及reached_target_unique，保留所有旧统计字段 |
| preflight_validation.py | 历史独立验证工具的统计使用其已请求目标；没有新增请求或修改验证协议 |
| test_comments_images_preflight.py | 扩展HTTP、Plan、统计、保存、恢复及非法值测试 |

Adapter原本已经支持三种目标，代码无需修改。CLI原本恢复时已从Plan显式读取top_comment_target，保留该实现及用户学习注释。新增统计随现有response_summary_json持久化，不改变18表DDL或Trigger。

## 合理保留的固定20

- top_comment_target默认值20，未改为30。
- reached_20_unique始终表达“唯一一级评论数至少20”，不能随配置变化。
- Normalizer两参数历史调用的默认统计基准仍为20；正式Collector始终显式传参，不依赖此默认。独立Preflight的默认/20对照协议也仍保留。
- 允许值集合20/25/30、历史报告、测试默认行为断言属于明确边界或历史证据，不批量替换。

例如目标30、实际25个唯一一级ID：`target_unique=30`、`reached_target_unique=false`、`reached_20_unique=true`。重复ID、缺失ID、实际返回数量、二级数量、has_more等原统计继续保留。

## Run与历史兼容性

新Run的请求配置和Plan均由同一实例生成。恢复依旧读取原Plan，并执行完整Run/config匹配；没有绕过校验。离线CLI测试分别保存目标20、25、30，再模拟程序默认值变化，恢复详情仍全部使用原目标且不重新搜索。

若历史Plan本身存在top_comment_target与嵌套max_comment_items冲突，当前逻辑明确拒绝恢复，不猜测、不重写Plan、不悄悄改变实际目标。此拒绝路径也有测试。

最终只读检查发现当前正式库已经有Run 1：状态running，搜索请求1 failed、49 pending，详情Attempt为0。其保存的top_comment_target和max_comment_items均为20，按原Plan重建的配置与当前persisted()完整一致。该Run在本轮开始前即已存在（正式库前后SHA-256相同），没有在本轮创建、恢复或修改。未来恢复此Run仍使用20，不能靠改变程序默认值将它静默改成30。

Pilot继续使用历史默认返回模式，detail_options为空，没有额外comment_config；旧软目标10等不会因正式目标校验被拒绝。Phase1C默认调用与入库逻辑不变。实际返回超过目标仍完整保存，父子Observation关联保持。

## 离线验证

使用内存SQLite、合成响应和临时普通文件，无真实HTTP/MCP请求。测试保护模式拒绝磁盘SQLite及真实网络，临时设置的环境变量执行后清除。

```powershell
$env:MARKETLENS_TEST_MEMORY_ONLY='1'
python -m pytest tests/research/test_comments_images_preflight.py tests/research/test_final_preflight.py tests/research/test_storage_integration.py -q -r s --junitxml=local_data/phase1d_comment_target_refactor/results.xml
Remove-Item Env:MARKETLENS_TEST_MEMORY_ONLY
```

最终 **86 passed、0 failed、0 skipped，7.05秒**，包括35项扩展后的评论/图片安全测试。未重复累计首轮35项结果，未将此结果冒称为全仓库磁盘回归。

覆盖：20/25/30的Collector→真实Adapter方法→模拟HTTP请求体；实际保存的Plan与请求一致；30目标返回25的统计；超目标完整入库；父子评论；Pilot默认模式及历史软目标；三个保存目标在后来默认值改变后经CLI恢复；非法目标提前拒绝；不匹配和内部冲突的Plan拒绝；搜索与详情继续分阶段。旧存储/阶段契约亦通过相关内存回归。

## 保护与结论

四个既有SQLite文件前后SHA-256相同，数据库清单仍为原4个；正式库实际为 **1 Run、0 Dataset**，不能继续沿用上一轮0 Run的结论。正式库本轮前后哈希均为 `558ea69266e405f3a0e21ba086bb11fdd658383ae98b8037bb6146d3ace80565`。当前DDL文件SHA-256不变。原有未提交学习注释、上轮fixture修正和文档均保留，未Reset/Clean。

**参数链路及离线验证通过，但当前应先确认已有Run 1的用途和失败状态，不建议再次新建正式50组搜索。** 默认仍为20，本轮不自动修改目标为30、不创建研究数据库、不启动或恢复真实搜索/详情；任何后续采集均等待用户确认。
