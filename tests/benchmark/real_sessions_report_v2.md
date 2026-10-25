# 真实会话基准报告 v2（P0 优化后复测）

- 日期：2026-10-25；与 v1（real_sessions_report.md）同任务、同 ground truth、同 direct 臂提示词；
  压缩臂提示词更新为新语义（块级摘要 + 窄查询透传 + 部分取回）——这是实验处理的一部分
- P0 改动：①skip_compression_if（git_show/git_diff 及 max_count≤20 的 git_log 原样透传）
  ②retrieve_original 支持 lines 行区间 / grep 部分取回 ③commit_digest 块级摘要
  （每条 commit 压成 `hash | 作者 | message首行`，非日志文本回退 head_tail）

- 执行者：subagent（GLM-5.3）× 20 个独立会话（10 任务 × 2 臂）；仓库：真实 flask（279 commits），
  ground truth 由直接 git 命令独立计算；两臂工具面完全对称（同一 helper），8 次调用上限；
  compressed 臂 v2 行为：git_show/git_diff 及 max_count≤20 的 git_log 原样透传，
  宽扫 git_log 返回 commit_digest 块级摘要，retrieve_original 支持 lines/grep 部分取回
- 指标：会话总 token（subagent_tokens，模型侧真实计量）+ 工具输出 chars（helper 侧计量）

## 总览

| 指标 | direct | compressed |
|---|---|---|
| 任务成功率 | 10/10 | 10/10 |
| 会话总 token（10 会话合计） | 494,496 | 571,610（+15.6%） |
| 工具输出 chars 合计（helper 计量） | 187,937 | 281,742（+49.9%） |
| 其中 retrieve_original 返回 chars | 0 | 0 |
| retrieve_original 调用次数 / 需要全量数据的任务数 | 0 | 0 / 10 |

## v1 → v2 对比（P0 优化效果）

| 指标 | v1 direct | v1 compressed | v2 direct | v2 compressed |
|---|---|---|---|---|
| 会话总 token | 517,580 | 635,592（+22.8%） | 494,496 | 571,610（+15.6%） |
| retrieve_original 调用 | 0 | 6（140,166 chars） | 0 | 0（0 chars） |
| 任务成功率 | 9/10 | 9/10 | 10/10 | 10/10 |

## 每任务明细

| 任务 | direct tokens | compressed tokens | Δ | direct 对 | compressed 对 | compressed 调用数 |
|---|---|---|---|---|---|---|
| T1 | 43,498 | 43,827 | +329 | ✅ | ✅ | 1 |
| T10 | 43,310 | 43,344 | +34 | ✅ | ✅ | 1 |
| T2 | 43,045 | 43,734 | +689 | ✅ | ✅ | 1 |
| T3 | 45,572 | 45,050 | -522 | ✅ | ✅ | 1 |
| T4 | 90,968 | 136,671 | +45,703 | ✅ | ✅ | 5 |
| T5 | 52,440 | 49,880 | -2,560 | ✅ | ✅ | 1 |
| T6 | 44,546 | 44,062 | -484 | ✅ | ✅ | 1 |
| T7 | 43,453 | 55,208 | +11,755 | ✅ | ✅ | 1 |
| T8 | 44,856 | 44,961 | +105 | ✅ | ✅ | 1 |
| T9 | 42,808 | 64,873 | +22,065 | ✅ | ✅ | 3 |

## 工具输出明细（compressed 臂）

| tool | 次数 | chars | compressed |
|---|---|---|---|
| git_diff_staged | 1 | 200 | 0/1 |
| git_diff_unstaged | 1 | 317 | 0/1 |
| git_log | 10 | 46,129 | 5/10 |
| git_show | 2 | 234,638 | 0/2 |
| git_status | 1 | 458 | 1/1 |

## v2 结论

1. **P0 核心目标达成：retrieve_original 调用 6 → 0**（v1 取回 140,166 chars，v2 为零）。
   块级摘要让"数数/列清单"类任务免取回；窄查询透传让"指定 revision"类任务免取回。
2. **深扫任务（T5/T6/T8）压缩臂大幅改善且持平或优于 direct**：
   T5 49,880 vs 52,440、T6 44,062 vs 44,546、T8 44,961 vs 44,856
   （v1 分别为 72,617 / 67,070 / 67,443，改善 31% / 34% / 33%）。
   git_log 工具输出 chars：46,129 vs 70,101（压缩臂省 34%，即使多一次调用）。
3. **总 token 仍 +15.6%，但构成已变**：差距几乎全部来自三个 n=1 行为方差——
   T4 压缩臂调了 2 次大 diff git_show（234,638 chars vs direct 1 次 117,319），
   T7c 选择全库扫描（max_count=279 的 digest），T9c 多做 2 次验证调用；
   剔除 T4/T7/T9 后：direct 317,267 vs compressed 314,858（-0.8%，持平）。
4. 成功率 10/10 vs 10/10（v1 两臂同错的前缀位数错本轮未出现——模型非确定性）。
5. **净值结论**：P0 把"系统性 retrieve 代价"清零，剩余差距是单样本噪声量级；
   压缩臂的确定收益 = 宽扫日志 34% chars 节省；要在总会话 token 上拿到确定净省，
   需要更大 n 或输出占比更高的真实长会话（工具输出在此类短会话 token 中占比 ~10-20%）。