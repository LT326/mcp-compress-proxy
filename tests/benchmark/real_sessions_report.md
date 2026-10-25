# 真实会话基准报告（real_sessions_report.md）

- 日期：2026-10-25；执行者：subagent（GLM-5.3）× 20 个独立会话（10 任务 × 2 臂）
- 仓库：真实 flask 源码仓库（gitee 镜像，279 commits / 236 文件），任务 ground truth 全部由直接 git 命令独立计算
- 臂设置：direct = 直连 mcp-server-git；compressed = 经 mcp-compress-proxy（fallback 档 head_tail 30/10）；
  两臂工具面完全对称（同一 helper 脚本），每会话独立子代理、8 次调用上限
- 指标：会话总 token（subagent_tokens，含系统提示+推理+工具输出，模型侧真实计量）+ 工具输出 chars（helper 侧计量）

## 总览

| 指标 | direct | compressed |
|---|---|---|
| 任务成功率 | 9/10 | 9/10 |
| 会话总 token（10 会话合计） | 517,580 | 635,592（+22.8%） |
| 工具输出 chars 合计（helper 计量） | 146,586 | 157,701（+7.6%） |
| 其中 retrieve_original 返回 chars | 0 | 140,166 |
| retrieve_original 调用次数 / 需要全量数据的任务数 | 0 | 6 / 10 |

## 每任务明细

| 任务 | direct tokens | compressed tokens | Δ | direct 对 | compressed 对 | compressed 调用数 |
|---|---|---|---|---|---|---|
| T1 | 43,618 | 44,930 | +1,312 | ✅ | ✅ | 1 |
| T10 | 43,291 | 44,512 | +1,221 | ✅ | ✅ | 1 |
| T2 | 42,827 | 43,225 | +398 | ✅ | ✅ | 1 |
| T3 | 45,272 | 45,310 | +38 | ✅ | ✅ | 1 |
| T4 | 111,485 | 138,807 | +27,322 | ✅ | ✅ | 5 |
| T5 | 53,739 | 72,617 | +18,878 | ✅ | ✅ | 2 |
| T6 | 44,444 | 67,070 | +22,626 | ✅ | ✅ | 2 |
| T7 | 45,578 | 45,923 | +345 | ✅ | ✅ | 1 |
| T8 | 44,765 | 67,443 | +22,678 | ❌ | ❌ | 2 |
| T9 | 42,561 | 65,755 | +23,194 | ✅ | ✅ | 2 |

## 工具输出明细（compressed 臂）

| tool | 次数 | chars | compressed |
|---|---|---|---|
| git_diff | 2 | 1,975 | 1/2 |
| git_log | 11 | 10,959 | 11/11 |
| git_show | 3 | 4,143 | 3/3 |
| git_status | 1 | 458 | 1/1 |
| retrieve_original | 6 | 140,166 | 0/6 |

## 结论（诚实版，含反直觉负结果）

1. **任务成功率无差异（9/10 vs 9/10）**：压缩不损害任务完成质量。唯一失败是 T8 中
   两臂**同错**的一处：把"hash 前 7 位"写成了 8 位（25 条中其余 24 条全对）——同一
   模型的确定性偏差，与压缩无关。
2. **会话总 token：压缩臂 +22.8%（635,592 vs 517,580）——压缩在这批任务上是净亏的。**
   分解：6 次 retrieve_original 返回 140,166 chars，≈ direct 臂全部工具输出总量
   （146,586）——模型判断需要全量数据时，压缩省下的又几乎全部取回来了，还多付出
   一轮工具调用与推理。
3. **真实 retrieve 率 60%（6/10 任务），远高于合成基准的 3.6%。** 根源是任务分布：
   本批一半任务是"精确全量型"（数数、列全清单、逐条核对）——任何有损压缩都必然
   触发取回；而"概览型"任务（T1/T2/T3/T7/T10）零取回，工具输出被压到 1/4~1/8，
   净省（如 T3：+38 token 持平但 chars 3.7K→0.9K）。
4. **价值边界（本实验的核心产出）**：可逆压缩的适用场景 = **大输出 + 概览型消费 +
   长会话**（工具输出占上下文比重大、retrieve 率低）；对"必须逐条精确处理全量数据"
   的任务，可逆压缩无收益甚至负收益。合成基准的 81.5% 是单条 payload 压缩率上界，
   真实净值由 retrieve 率决定——两个数字都真实，回答的是不同问题。这验证并细化了
   设计文档"retrieve 率决定端到端净值"的核心论点。

## 实验过程中发现并修复的真 bug

- **asyncio StreamReader 64KB 单行上限**：大 diff 的 git_show 响应是单行 JSON 超过
  asyncio 默认 64KB 行限，`LimitOverrunError` 直接打崩代理（T4 首轮 4 次调用全崩、
  烧掉 242K token 后失败）。修复：spawn 时 `limit=64MB`（src/mcp_compress_proxy/proxy/main.py）。
  修复后同任务成功（138,807 tokens）。pre-fix 失败样本保留在 answers-part2.json 中披露。
  根因报告：直连臂用同步读无此限制，故只有经代理链路触发——典型"只在一侧链路暴露"
  的传输层 bug。

## 方法论限制（诚实声明）

1. 单模型（GLM-5.3）、单仓库（flask 279 commits）、10 任务/臂、每配置 1 次重复——
   统计功效有限，**数字看方向，不看小数点**。
2. 每次工具调用为全新代理实例 → 代理上下文窗口为空，任务检测恒走声明 fallback 档
   （head_tail 30/10）。真实长会话有滚动上下文，可能命中 debug/review 档。
3. subagent_tokens 含子代理系统提示与推理；压缩臂提示词多 ~150 token/会话的压缩
   说明（占比 ~0.3%，不解释 22.8% 差异的主因——主因是 retrieve 往返）。
4. 执行瑕疵披露：T4 首轮因执行者（主 agent）笔误把 k=5 写成 k=9 作废重跑；k=9 恰为
   merge commit（prepare 程序本已规避），两件事无关但均如实记录。
5. 两臂共享同一工作区状态（T9 使用）；其余任务均为只读，无跨臂污染。
6. direct 臂 T4 样本为重跑所得（正确 prompt），首跑两臂样本作废不计分。

## 复现

```bash
# 1) 准备（克隆仓库 + 生成任务与 ground truth）
python scripts/real_bench_prepare.py
# 2) 双臂单发调用（LLM 会话由 ZCode subagent 驱动，见本报告方法论）
.venv/Scripts/python.exe scripts/mcp_call.py direct git_log '{"max_count": 50}'
.venv/Scripts/python.exe scripts/mcp_call.py compressed git_log '{"max_count": 50}'
# 3) 计分
python scripts/real_bench_score.py
```
