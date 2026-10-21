# MarketLens Phase 1D — Run 1 最终报告

生成时间：2026-10-20T12:30:56.908+00:00（UTC）。本文与同目录 `run1_metrics.json`由同一聚合结果生成，不包含评论原文或作者信息。

## 1. 实验目标与采样设计

建立可追溯的研究基线，观察既定关键词与排序下的候选及有限评论样本；不估计平台总体、销量或市场份额。

研究范围：Project 1，Run 1。关键词10个、排序5种、实际组合50组；图文筛选仍可能返回视频。

采集配置（来源collection_plans.comment_config_json）：`{"category": "粉底液", "comment_config": {"click_more_replies": false, "max_comment_items": 20, "max_replies_threshold": 10, "scroll_speed": "normal"}, "extra_comment_requests": 0, "interval_seconds": 3.0, "load_all_comments": true, "selection": "first_search_then_result_position", "top_comment_target": 20}`。配置目标不是成功与否的判据。

## 2. 数据采集流程概述

先搜索并保留各次Hit，再按首次搜索及结果位置安排图文详情；保留真实Attempt、Raw及评论观察历史。失败按既有策略重试或人工复核，最后显式本地封口；Dataset冻结是后续独立步骤。

Dataset独立验证同时复核Run原始证据。Run Raw记录533份，声明字节合计6,087,278；媒体通过本地哈希、文件大小及图像解码尺寸校验。

## 3. 最终数据规模与统计边界

| Run指标 | 数值 | 来源/定义 |
| --- | --- | --- |
| 成功搜索组 / 搜索Attempt | 50 / 60 | search_requests状态 / 实际持久化Attempt |
| 搜索命中 | 975 | Run内search_hits行数 |
| 唯一候选 | 487 | 去重post_id |
| 重复命中 | 488 | Hit数−唯一候选数 |
| 重复命中率 | 50.05% | 重复命中 / 全部Hit |
| 成功详情Task | 462 | 成功normal Snapshot可核实 |
| 来源删除 | 1 | 审核证据验证，不从超时推断 |
| 人工受限永久跳过 | 1 | 与来源删除分开 |
| 未解决详情Task | 0 | 成功观察及终结证据分类 |
| 图文详情成功率 | 99.57% | 拥有成功Snapshot的图文候选 / 图文候选 |
| 成功Snapshot / 唯一Post | 462 / 462 | 观察与身份分开 |
| 同Post多Snapshot | 0 | Run内成功Snapshot数>1的Post |

| 冻结Dataset指标 | 数值 |
| --- | --- |
| 候选台账 | 487 |
| included | 462 |
| detail_unavailable | 2 |
| type_mismatch | 23 |
| 选定Snapshot | 462 |
| 评论Observation | 10,246 |
| 稳定Comment身份 | 10,246 |
| 匿名Observation | 0 |
| 已验证图片关联 | 1,677 |
| 已验证独立图片文件 | 1,677 |
| 独立图片文件字节 | 177,890,618 |

Run保留全部成功观察；Dataset按冻结成员引用固定Snapshot/Observation。Post与Snapshot是一对多，当前数量相同不意味着Schema是一对一。图片是关联数，不自动代表不同文件数。

## 4. 关键词、排序与发现增量

下列表来源Run Analytics search_sampling_metrics。独有贡献指仅由该关键词或排序发现，与执行顺序无关；边际新增指相对之前所有搜索组尚未出现，依赖sequence_no/search_request_id顺序。各关键词的唯一数存在交叉，不能相加作为全体唯一候选数。

| 关键词 | 命中 | 唯一候选 | 独有贡献 |
| --- | --- | --- | --- |
| 粉底液使用感受 | 99 | 77 | 41 |
| 粉底液测评 | 100 | 68 | 26 |
| 粉底液踩雷 | 100 | 60 | 52 |
| 粉底液推荐 | 100 | 76 | 38 |
| 粉底液种草 | 100 | 74 | 36 |
| 粉底液回购 | 78 | 43 | 30 |
| 粉底液盘点 | 99 | 76 | 34 |
| 粉底液对比 | 100 | 71 | 28 |
| 粉底液 | 100 | 78 | 29 |
| 粉底液购买 | 99 | 78 | 55 |

| 排序 | 命中 | 唯一候选 | 独有贡献 |
| --- | --- | --- | --- |
| 综合 | 180 | 121 | 81 |
| 最新 | 195 | 171 | 156 |
| 最多点赞 | 200 | 132 | 30 |
| 最多评论 | 200 | 150 | 57 |
| 最多收藏 | 200 | 123 | 27 |

排序集合重叠：Jaccard=交集/并集，空并集返回未知；不等同于重复Hit率。

| 排序A | 排序B | 交集 | 并集 | Jaccard |
| --- | --- | --- | --- | --- |
| 综合 | 最新 | 9 | 283 | 3.18% |
| 综合 | 最多点赞 | 15 | 238 | 6.30% |
| 综合 | 最多评论 | 26 | 245 | 10.61% |
| 综合 | 最多收藏 | 14 | 230 | 6.09% |
| 最新 | 最多点赞 | 8 | 295 | 2.71% |
| 最新 | 最多评论 | 5 | 316 | 1.58% |
| 最新 | 最多收藏 | 5 | 289 | 1.73% |
| 最多点赞 | 最多评论 | 69 | 213 | 32.39% |
| 最多点赞 | 最多收藏 | 82 | 173 | 47.40% |
| 最多评论 | 最多收藏 | 62 | 211 | 29.38% |

| 顺序 | 关键词 | 排序 | Hit | 本组唯一 | 边际新增 | 累计唯一 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 粉底液使用感受 | 综合 | 20 | 20 | 20 | 20 |
| 2 | 粉底液使用感受 | 最新 | 19 | 19 | 19 | 39 |
| 3 | 粉底液使用感受 | 最多点赞 | 20 | 20 | 20 | 59 |
| 4 | 粉底液使用感受 | 最多评论 | 20 | 20 | 12 | 71 |
| 5 | 粉底液使用感受 | 最多收藏 | 20 | 20 | 6 | 77 |
| 6 | 粉底液测评 | 综合 | 20 | 20 | 15 | 92 |
| 7 | 粉底液测评 | 最新 | 20 | 20 | 17 | 109 |
| 8 | 粉底液测评 | 最多点赞 | 20 | 20 | 11 | 120 |
| 9 | 粉底液测评 | 最多评论 | 20 | 20 | 6 | 126 |
| 10 | 粉底液测评 | 最多收藏 | 20 | 20 | 3 | 129 |
| 11 | 粉底液踩雷 | 综合 | 20 | 20 | 14 | 143 |
| 12 | 粉底液踩雷 | 最新 | 20 | 20 | 17 | 160 |
| 13 | 粉底液踩雷 | 最多点赞 | 20 | 20 | 15 | 175 |
| 14 | 粉底液踩雷 | 最多评论 | 20 | 20 | 5 | 180 |
| 15 | 粉底液踩雷 | 最多收藏 | 20 | 20 | 3 | 183 |
| 16 | 粉底液推荐 | 综合 | 20 | 20 | 11 | 194 |
| 17 | 粉底液推荐 | 最新 | 20 | 20 | 17 | 211 |
| 18 | 粉底液推荐 | 最多点赞 | 20 | 20 | 11 | 222 |
| 19 | 粉底液推荐 | 最多评论 | 20 | 20 | 11 | 233 |
| 20 | 粉底液推荐 | 最多收藏 | 20 | 20 | 4 | 237 |
| 21 | 粉底液种草 | 综合 | 20 | 20 | 9 | 246 |
| 22 | 粉底液种草 | 最新 | 20 | 20 | 17 | 263 |
| 23 | 粉底液种草 | 最多点赞 | 20 | 20 | 9 | 272 |
| 24 | 粉底液种草 | 最多评论 | 20 | 20 | 10 | 282 |
| 25 | 粉底液种草 | 最多收藏 | 20 | 20 | 2 | 284 |
| 26 | 粉底液回购 | 综合 | 0 | 0 | 0 | 284 |
| 27 | 粉底液回购 | 最新 | 18 | 18 | 17 | 301 |
| 28 | 粉底液回购 | 最多点赞 | 20 | 20 | 14 | 315 |
| 29 | 粉底液回购 | 最多评论 | 20 | 20 | 7 | 322 |
| 30 | 粉底液回购 | 最多收藏 | 20 | 20 | 0 | 322 |
| 31 | 粉底液盘点 | 综合 | 20 | 20 | 13 | 335 |
| 32 | 粉底液盘点 | 最新 | 19 | 19 | 16 | 351 |
| 33 | 粉底液盘点 | 最多点赞 | 20 | 20 | 10 | 361 |
| 34 | 粉底液盘点 | 最多评论 | 20 | 20 | 5 | 366 |
| 35 | 粉底液盘点 | 最多收藏 | 20 | 20 | 3 | 369 |
| 36 | 粉底液对比 | 综合 | 20 | 20 | 7 | 376 |
| 37 | 粉底液对比 | 最新 | 20 | 20 | 16 | 392 |
| 38 | 粉底液对比 | 最多点赞 | 20 | 20 | 5 | 397 |
| 39 | 粉底液对比 | 最多评论 | 20 | 20 | 4 | 401 |
| 40 | 粉底液对比 | 最多收藏 | 20 | 20 | 1 | 402 |
| 41 | 粉底液 | 综合 | 20 | 20 | 3 | 405 |
| 42 | 粉底液 | 最新 | 20 | 20 | 7 | 412 |
| 43 | 粉底液 | 最多点赞 | 20 | 20 | 8 | 420 |
| 44 | 粉底液 | 最多评论 | 20 | 20 | 11 | 431 |
| 45 | 粉底液 | 最多收藏 | 20 | 20 | 1 | 432 |
| 46 | 粉底液购买 | 综合 | 20 | 20 | 11 | 443 |
| 47 | 粉底液购买 | 最新 | 19 | 19 | 17 | 460 |
| 48 | 粉底液购买 | 最多点赞 | 20 | 20 | 11 | 471 |
| 49 | 粉底液购买 | 最多评论 | 20 | 20 | 6 | 477 |
| 50 | 粉底液购买 | 最多收藏 | 20 | 20 | 10 | 487 |

## 5. 评论覆盖与字段质量

| 评论指标 | Run成功观察 | 冻结Dataset成员 |
| --- | --- | --- |
| 一级评论 | 6,589 | 6,589 |
| 二级回复 | 3,657 | 3,657 |
| 全部Observation | 10,246 | 10,246 |
| 稳定身份 | 10,246 | 10,246 |
| 重复稳定身份的额外观察 | 0 | 0 |
| 匿名观察 | 0 | 0 |
| 未观察到评论的笔记 | 58 | 58 |
| 达到目标 | 290 | 290 |
| 低于目标 | 172 | 172 |
| 超过目标 | 0 | 0 |
| 目标未知 | 0 | 0 |
| 目标达成比例 | 62.77% | 62.77% |

目标以唯一稳定一级评论ID计算，未知目标不进入比例分母。Run每篇分布取最新成功Snapshot所属Attempt；Dataset取其全部冻结评论成员。匿名Observation单列、不按正文合并。

| 一级评论分布 | Run每篇 | Dataset每篇 |
| --- | --- | --- |
| n | 462 | 462 |
| sum | 6,589 | 6,589 |
| mean | 14.262 | 14.262 |
| min | 0 | 0 |
| p25 | 5.250 | 5.250 |
| median | 20.000 | 20.000 |
| p75 | 20.000 | 20.000 |
| p90 | 20.000 | 20.000 |
| p95 | 20.000 | 20.000 |
| max | 20 | 20 |

分位数采用linear插值，rank=(n−1)p；零评论笔记进入每篇分母。Dataset每篇一级评论频数：

| 一级评论数 | 笔记数 |
| --- | --- |
| 0 | 58 |
| 1 | 22 |
| 2 | 14 |
| 3 | 7 |
| 4 | 7 |
| 5 | 8 |
| 6 | 7 |
| 7 | 2 |
| 8 | 7 |
| 9 | 1 |
| 10 | 5 |
| 11 | 9 |
| 12 | 5 |
| 13 | 1 |
| 14 | 3 |
| 15 | 4 |
| 16 | 5 |
| 17 | 2 |
| 18 | 4 |
| 19 | 1 |
| 20 | 290 |

| has_more | Run最新Snapshot的Attempt | Dataset选定Snapshot的Attempt |
| --- | --- | --- |
| true | 289 | 289 |
| false | 173 | 173 |
| unknown | 0 | 0 |

has_more是源响应元数据，不证明Dataset或平台评论区完整；未观察到评论不等于原帖没有评论。

Run父子关系异常：`{"identity_binding": 0, "parent_identity": 0, "parent_observation": 0, "parent_source_id": 0}`；Dataset保留原始父Observation，同时按稳定父身份追溯，跨观察父链接数：0。

冻结一级评论所保存的父响应回复覆盖（来源comment_raw_json，复用normalizers.count）：

| 指标 | 数值 |
| --- | --- |
| parents | 6,589 |
| known_displayed_reply_count | 6,589 |
| parents_with_unreturned_replies | 2,184 |
| displayed_replies_sum | 20,695 |
| replies_returned_in_parent_response | 3,657 |
| positive_gap_sum | 17,038 |

展示回复数与该父响应返回回复数之间的正差不是全评论区缺失总量。当前规则未展开的回复仍然未观察；不能将这些评论作为完整总体。

字段完整性：Run按全部成功观察；Dataset按固定成员。文本NULL及纯空白均为缺失；数值NULL为未知、真实0保留；valid表示可用而非人工确认真实。

| 对象/字段 | Run观察数 | Run缺失 | Dataset观察数 | Dataset有效 | Dataset缺失 | 缺失率 | 真实0 | 展示精度 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| snapshots/comments_count | 462 | 59 | 462 | 403 | 59 | 12.77% | 0 | {"exact_display": 403, "unparseable": 59} |
| snapshots/description | 462 | 0 | 462 | 462 | 0 | 0.00% | 未知/不适用 | 不适用 |
| snapshots/favorites_count | 462 | 78 | 462 | 384 | 78 | 16.88% | 0 | {"exact_display": 384, "unparseable": 78} |
| snapshots/likes_count | 462 | 39 | 462 | 423 | 39 | 8.44% | 0 | {"approximate": 5, "exact_display": 418, "unparseable": 39} |
| snapshots/published_at | 462 | 0 | 462 | 462 | 0 | 0.00% | 未知/不适用 | 不适用 |
| snapshots/published_at_raw | 462 | 0 | 462 | 462 | 0 | 0.00% | 未知/不适用 | 不适用 |
| snapshots/shares_count | 462 | 462 | 462 | 0 | 462 | 100.00% | 0 | {"unparseable": 462} |
| snapshots/title | 462 | 20 | 462 | 442 | 20 | 4.33% | 未知/不适用 | 不适用 |
| comments/content | 10,246 | 86 | 10,246 | 10,160 | 86 | 0.84% | 未知/不适用 | 不适用 |
| comments/created_at | 10,246 | 0 | 10,246 | 10,246 | 0 | 0.00% | 未知/不适用 | 不适用 |
| comments/created_at_raw | 10,246 | 0 | 10,246 | 10,246 | 0 | 0.00% | 未知/不适用 | 不适用 |
| comments/like_count | 10,246 | 0 | 10,246 | 10,246 | 0 | 0.00% | 6,172 | {"approximate": 2, "exact_display": 10244} |

精确展示值、近似展示值及下界分别保留解析类别；不把未知互动数填成0，不因空文本删除原始成员。

## 6. 请求可靠性与异常恢复

来源collection_tasks/collection_attempts。首次成功指该Task最早Attempt成功；重试成功指最早未成功而后续成功。请求次数仅包含持久化采集Attempt，不含健康/登录预检查、媒体下载或服务内部请求。

| 类型 | Task | Attempt | 成功Attempt | 失败Attempt | 首次成功Task | 重试后成功Task |
| --- | --- | --- | --- | --- | --- | --- |
| feed_detail | 464 | 473 | 462 | 11 | 455 | 7 |
| search | 50 | 60 | 50 | 10 | 40 | 10 |

| 类型 | 错误类别 | HTTP状态 | 失败Attempt |
| --- | --- | --- | --- |
| feed_detail | HTTPError | 500 | 3 |
| feed_detail | TimeoutError | 未知/不适用 | 8 |
| search | HTTPError | 500 | 10 |

本地HTTP客户端耗时，单位秒；包括响应读取/解析，详情包含服务端评论加载，不等于平台内部单次HTTP耗时。

| 类型 | 状态 | n | 均值 | 中位数 | P90 | P95 |
| --- | --- | --- | --- | --- | --- | --- |
| feed_detail | all | 473 | 16.556 | 15.782 | 17.700 | 18.294 |
| feed_detail | failed | 11 | 85.041 | 120.000 | 120.031 | 120.031 |
| feed_detail | succeeded | 462 | 14.925 | 15.781 | 17.623 | 18.015 |
| search | all | 60 | 15.535 | 8.657 | 60.297 | 60.344 |
| search | failed | 10 | 49.203 | 60.312 | 60.359 | 60.360 |
| search | succeeded | 50 | 8.802 | 8.469 | 9.814 | 10.521 |

日志中明确记录的实际等待段（次数是段数，不是Task数）：

| 阶段 | 等待段 | 实际秒 | 缺失/无效时长 |
| --- | --- | --- | --- |
| after | 598 | 1,645.794 | 0 |
| batch | 8 | 1,301.346 | 0 |
| before | 218 | 1,763.143 | 0 |

| 日志 | 记录数 | 首记录UTC | 末记录UTC |
| --- | --- | --- | --- |
| detail_pacing | 2,090 | 2026-10-16T07:20:30.367Z | 2026-10-16T12:10:37.142Z |
| detail_retry | 964 | 2026-10-15T06:11:23.776Z | 2026-10-16T12:08:57.060Z |

日志可用性警告：无缺失日志警告；记录覆盖范围仍不等于全Run。

重试计划延迟：`{"actual_seconds": null, "missing_or_invalid_duration": 0, "planned_seconds": 594.8341826855178, "schedules": 9}`；计划时长不能当作实际等待。

有审核记录的不同Task：6个；ID：[153, 237, 300, 301, 302, 473]。保存的运行结果报告次数：`{"BLOCKED": 18, "PARTIAL": 2, "PAUSED": 1}`；这些不是完整暂停/恢复次数。

另排除3份缺少Run ID的运行输出、0份其他Run输出，不据此推断本Run的活动。

| 时间口径 | 数值/UTC |
| --- | --- |
| capture_span_seconds | 122,042.999 |
| creation_to_seal_seconds | 348,750.749 |
| first_attempt_started_at | 2026-10-15T02:14:44.988Z |
| last_attempt_finished_at | 2026-10-16T12:08:47.987Z |
| recorded_attempt_duration_sum_seconds | 8,762.914 |
| run_sealed_at | 2026-10-19T03:07:12.731Z |
| run_started_at | 2026-10-15T02:14:41.982Z |

首末Attempt跨度包含暂停和人工处理；创建至封口跨度还包含采集结束后的处理时间。不得用墙钟时间减去请求耗时反推等待，不提供全Run活跃运行时长。

## 7. Run封口与Dataset冻结

Run状态：`partial`；本地封口校验：`{"checkpoint_synchronized": true, "recovery_required": false, "sealed": true, "sha256": "b3b9f001e99cc5fd13cd1ae9e2534cfffa874554d74356d9f9008a4f64652e0d", "validated": true}`。

| Dataset属性 | 值 |
| --- | --- |
| created_at | 2026-10-20T11:59:34.819Z |
| dataset_id | 1 |
| dataset_name | phase1d_baseline |
| frozen_at | 2026-10-20T11:59:34.819Z |
| manifest_sha256 | 4981ccb17f74f4976a10cecdff05146434a156d9dbcb7315966c4f99d20e579b |
| project_id | 1 |
| source_run_id | 1 |
| status | frozen |
| version | v1 |

成员规则：`{"candidates": "all_source_run_hits_from_succeeded_or_partial_attempts", "comments": "latest_stable_identity_by_observed_at_then_observation_id;all_anonymous", "observation_cutoff_at": "2026-10-19T03:07:12.731Z", "quality": "retain_blank_text_null_metrics_and_original_parent_context", "rule_version": "dataset_selection_v1", "snapshots": "latest_normal_by_observed_at_then_snapshot_id_in_source_run"}`。稳定身份选截止前最新Observation；匿名观察全部保留。未来再次采集通过新Run，不重新计算并覆盖旧成员。

| 不可获取Task | 经证据验证的原因 |
| --- | --- |
| 153 | source_deleted |
| 473 | banned_or_restricted |

正式Manifest：`local_data/datasets/phase1d_baseline/v1/dataset_1_phase1d_baseline_v1.json`，3,941,025字节；SHA-256：`4981ccb17f74f4976a10cecdff05146434a156d9dbcb7315966c4f99d20e579b`。独立验证通过：`True`，simulation：`False`；完整性/外键：`{"foreign_key_check": [], "integrity_check": ["ok"]}`。

Dataset成员表及底层观察受不可变触发器保护；文件哈希之外，验证器还重新核对实际成员、规则、来源、Raw及媒体。

## 8. 采样偏差、缺失与研究局限

- 关键词、排序、有限搜索深度及采集时间共同决定样本，不是随机抽样，不能外推平台或市场总体。
- 搜索重复、视频混入及两类不可获取候选均保留台账；成功空响应不证明平台没有相关内容。
- 评论目标与自然返回数量不同，二级回复未完整展开；评论展示数也不等于本次观察数。
- 空文本和未知/近似互动指标可能影响下游分析，应按有效分母及精度报告，不能无依据插补。
- 日志仅覆盖已记录时段；完整暂停/恢复次数、全Run等待/活跃时间和内部平台耗时不能可靠计算。

## 9. 数据复现方法与版本

Run Analytics契约：`run_metrics_v1`；Run来源指纹：`b1f74bd90ea393234f848d93b1f3bbfe120ecbd996127d407868bc38f6bacd6f`。

Run统计复用`marketlens.research.run_metrics.analyze_run()`；Dataset验证复用`dataset_builder.verify_dataset()`；附加SQL只读取指定Dataset成员和指定Run的Task/Attempt。日志指纹见聚合JSON。

仓库HEAD：`43a7a4c425f9933d430c32ca9c8764869152701a`。HEAD仅作基点，未声称未提交代码已经纳入该Commit；实际实现以文件SHA-256标识：

| 实现文件 | SHA-256 |
| --- | --- |
| marketlens/research/dataset_builder.py | 31f456855dd99256c117d1efaee623f34b33166efdd700b5653461d832ab8311 |
| marketlens/research/normalizers.py | 6ebec0f257dfcbfa4e4b2341e2afed3284b934a7ea101f62dc6795d4831ae4d3 |
| marketlens/research/run_metrics.py | c37578c035804dc32c1a5c09585bd7f7d889ae7a30cf96b5946e96733b1e81bd |
| marketlens/research/run_report.py | 7bd17fc32ffe52f157d9349cc3c1efcaf5ba9925b6d57d00bf3394e7769b57fd |

在项目根目录运行（只读正式数据，只写报告目录）：

```powershell
python -B -m marketlens.research.run_report --database 'local_data/phase1d_formal_baseline/research_baseline.db' --data-dir 'local_data/phase1d_formal_baseline' --manifest-dir 'local_data/datasets/phase1d_baseline/v1' --project-id 1 --run-id 1 --dataset-id 1 --output-dir docs/phase1d_xhs_baseline_collection
```

复现需要相同数据库、Manifest、Raw、媒体及审核证据；仅有聚合JSON不能重建原始研究数据。封口决定绑定原数据库路径，迁移副本需保留原决定并验证权威来源，不能修改绑定以通过检查。聚合报告不包含原文、作者信息或认证缓存。
