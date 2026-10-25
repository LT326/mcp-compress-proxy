# 压缩基准报告 report.md（v1.0）

- 生成时间：2026-10-24T14:51:24.118528+00:00
- fixtures：7 份，全部 **synthetic**（`tests/fixtures/gen_fixtures.py` 固定种子 20261024 生成，无 real 样本）
- 指标定义：压缩率 = 1 - compressed_chars/original_chars；保留率 = critical paths 值一致比例；
  retrieve 率 = 需要 retrieve_original 的组合占比（人工标注 + 保留率未达标自动计入）；
  net = (compressed_chars + retrieve_chars) / original_chars

## 目标达成

| 指标 | 目标 | 实际 | 结果 |
|---|---|---|---|
| 平均压缩率 | >= 65% | 81.5% | PASS |
| 关键字段保留率（最低） | 100% | 100% | PASS |
| retrieve_original 调用率 | < 15% | 3.6% | PASS |
| 平均端到端净值 | 越低越好 | 0.22 | - |

## 分 server 汇总

| server | 组合数 | 平均压缩率 | 最低保留率 | retrieve 数 |
|---|---|---|---|---|
| git | 12 | 89.0% | 100% | 0 |
| filesystem | 8 | 83.2% | 100% | 1 |
| terminal | 8 | 68.5% | 100% | 0 |

## 明细（fixture × task_type）

| fixture | task | 压缩率 | 保留率 | retrieved | net | 原始 chars | 压缩 chars |
|---|---|---|---|---|---|---|---|
| git_log_debug.json | debug | 94.0% | 100% | no | 0.06 | 23395 | 1409 |
| git_log_debug.json | review | 79.7% | 100% | no | 0.20 | 23395 | 4753 |
| git_log_debug.json | explore | 96.3% | 100% | no | 0.04 | 23395 | 863 |
| git_log_debug.json | unknown | 84.9% | 100% | no | 0.15 | 23395 | 3534 |
| git_log_review.json | debug | 96.0% | 100% | no | 0.04 | 35077 | 1409 |
| git_log_review.json | review | 79.8% | 100% | no | 0.20 | 35077 | 7091 |
| git_log_review.json | explore | 97.5% | 100% | no | 0.02 | 35077 | 863 |
| git_log_review.json | unknown | 89.9% | 100% | no | 0.10 | 35077 | 3534 |
| git_diff_large.json | debug | 71.7% | 100% | no | 0.28 | 18036 | 5108 |
| git_diff_large.json | review | 89.6% | 100% | no | 0.10 | 18036 | 1880 |
| git_diff_large.json | explore | 94.9% | 100% | no | 0.05 | 18036 | 920 |
| git_diff_large.json | unknown | 94.1% | 100% | no | 0.06 | 18036 | 1056 |
| fs_list_large.json | debug | 67.4% | 100% | no | 0.33 | 34737 | 11334 |
| fs_list_large.json | review | 41.0% | 100% | no | 0.59 | 34737 | 20497 |
| fs_list_large.json | explore | 59.4% | 100% | no | 0.41 | 34737 | 14097 |
| fs_list_large.json | unknown | 98.3% | 100% | no | 0.02 | 34737 | 577 |
| fs_read_large.txt | debug | 99.4% | 100% | no | 0.01 | 148734 | 963 |
| fs_read_large.txt | review | 99.9% | 100% | **yes** | 1.00 | 148734 | 110 |
| fs_read_large.txt | explore | 100.0% | 100% | no | 0.00 | 148734 | 62 |
| fs_read_large.txt | unknown | 99.9% | 100% | no | 0.00 | 148734 | 110 |
| terminal_test_output.txt | debug | 70.1% | 100% | no | 0.30 | 7219 | 2156 |
| terminal_test_output.txt | review | 93.6% | 100% | no | 0.06 | 7219 | 462 |
| terminal_test_output.txt | explore | 85.8% | 100% | no | 0.14 | 7219 | 1027 |
| terminal_test_output.txt | unknown | 22.4% | 100% | no | 0.78 | 7219 | 5600 |
| terminal_build_log.txt | debug | 64.7% | 100% | no | 0.35 | 6098 | 2150 |
| terminal_build_log.txt | review | 98.7% | 100% | no | 0.01 | 6098 | 77 |
| terminal_build_log.txt | explore | 92.1% | 100% | no | 0.08 | 6098 | 480 |
| terminal_build_log.txt | unknown | 20.1% | 100% | no | 0.80 | 6098 | 4873 |

## retrieve_original 标注明细

- **fs_read_large.txt × review**：代码内容评审需要文件正文；v1.0 profile 按规格 drop content，必须取回 —— 已知张力，见报告 v1.1 建议

## 失败案例（保留率 <100% 或压缩率 <40%）

- terminal_test_output.txt × unknown：retention=100%, compression=22.4%, missed=-
- terminal_build_log.txt × unknown：retention=100%, compression=20.1%, missed=-

## 与基线对比（平均压缩率）

| 方案 | 平均压缩率 |
|---|---|
| 无压缩基线 | 0.0% |
| 通用 fallback（顶层键 + 数组前 3 项） | 50.2% |
| **任务感知 Profile（本方案）** | **81.5%** |

## 已知边界与 v1.1 建议

1. **fs read × review 张力**：规格要求 review drop content，但代码内容评审必须看正文 →
   该组合 retrieve=yes，net=1.00 反而高于原始。建议 v1.1 给 filesystem review 增加
   `content: first_line` 或 diff-aware 保留。
2. **代理上下文弱信号**：代理只能从 tools/call 参数提取文本（AGENTS.md R9），真实链路中
   unknown → 声明 fallback 的触发率会高于本基准（本基准直接指定 task_type）。
3. **MCP initialize 不含工具列表**：注入点实际是 tools/list（initialize 已做防御式兼容）。
4. 通用 fallback 对「大字符串字段」无压缩能力（顶层键整保留）——已被三个 server 的
   声明式 fallback 覆盖，但接入第四个 server 时必须先写声明 fallback。
