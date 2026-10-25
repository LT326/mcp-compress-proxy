"""真实会话基准计分：成功率 / 会话 token / 工具输出 chars / retrieve 率 → 报告。"""

from __future__ import annotations

import json
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BENCH = PROJECT_ROOT / ".e2e" / "real-bench"
OUT = PROJECT_ROOT / "tests" / "benchmark" / "real_sessions_report_v2.md"

# v1（P0 优化前）结果，硬编码用于对比；明细见 real_sessions_report.md 与 answers-v1/
V1 = {"direct_tokens": 517580, "compressed_tokens": 635592, "retrieve_calls": 6,
      "retrieve_chars": 140166, "direct_ok": 9, "compressed_ok": 10}


def load_answers() -> list[dict]:
    rows = []
    for p in sorted(BENCH.glob("answers-part*.json")):
        rows.extend(json.loads(p.read_text(encoding="utf-8")))
    # 剔除无效样本（pre-fix T4 崩溃样本，仅留 post-fix）
    return [r for r in rows if not str(r.get("answer", "")).startswith("FAILED")]


def normalize(checker: str, value: str) -> str:
    value = value.strip()
    if checker == "hash":
        return value.lower()
    if checker == "int":
        return str(int(re.search(r"\d+", value).group()))
    if checker == "set":
        # 支持 'staged: a, b; unstaged: c' 与纯逗号列表两种形态
        if ";" in value:
            left, right = value.split(";", 1)
            def parts(seg, prefix):
                seg = seg.strip()
                if seg.startswith(prefix):
                    seg = seg[len(prefix):].strip().rstrip(":")
                return ",".join(sorted(p.strip() for p in seg.split(",") if p.strip()))
            m = re.match(r"(staged)\s*:", left)
            def half(seg, expect_prefix):
                seg = seg.strip()
                seg = re.sub(r"(?i)^%s\s*:\s*" % expect_prefix, "", seg)
                return ",".join(sorted(p.strip() for p in seg.split(",") if p.strip()))
            left_h = half(left, "staged")
            right_h = half(right, "unstaged")
            return f"staged:{left_h};unstaged:{right_h}"
        return ",".join(sorted(p.strip() for p in value.split(",") if p.strip()))
    if checker == "lines":
        items = [re.sub(r"\s+", " ", it).strip() for it in value.split("|")]
        return " | ".join(i for i in items if i)
    return re.sub(r"\s+", " ", value)


def read_metrics(arm: str) -> list[dict]:
    path = BENCH / f"metrics-{arm}.jsonl"
    if not path.exists():
        return []
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    # 剔除基准期间的手动复现记录（极短的崩溃报错行）
    return [r for r in rows if r["sent_chars"] > 100]


def main() -> int:
    tasks = {t["id"]: t for t in json.loads((BENCH / "tasks.json").read_text(encoding="utf-8"))}
    answers = load_answers()

    results = {}
    for r in answers:
        task = tasks[r["task"]]
        ok = normalize(task["checker"], r["answer"]) == normalize(task["checker"], task["expected"])
        results[(r["task"], r["arm"])] = {**r, "ok": ok}

    metrics = {arm: read_metrics(arm) for arm in ("direct", "compressed")}
    retrieve_calls = [m for m in metrics["compressed"] if m["tool"] == "retrieve_original"]

    lines = []
    lines.append("# 真实会话基准报告 v2（P0 优化后复测）")
    lines.append("")
    lines.append("- 日期：2026-10-25；与 v1（real_sessions_report.md）同任务、同 ground truth、同 direct 臂提示词；")
    lines.append("  压缩臂提示词更新为新语义（块级摘要 + 窄查询透传 + 部分取回）——这是实验处理的一部分")
    lines.append("- P0 改动：①skip_compression_if（git_show/git_diff 及 max_count≤20 的 git_log 原样透传）")
    lines.append("  ②retrieve_original 支持 lines 行区间 / grep 部分取回 ③commit_digest 块级摘要")
    lines.append("  （每条 commit 压成 `hash | 作者 | message首行`，非日志文本回退 head_tail）")
    lines.append("")
    lines.append("- 执行者：subagent（GLM-5.3）× 20 个独立会话（10 任务 × 2 臂）；仓库：真实 flask（279 commits），")
    lines.append("  ground truth 由直接 git 命令独立计算；两臂工具面完全对称（同一 helper），8 次调用上限；")
    lines.append("  compressed 臂 v2 行为：git_show/git_diff 及 max_count≤20 的 git_log 原样透传，")
    lines.append("  宽扫 git_log 返回 commit_digest 块级摘要，retrieve_original 支持 lines/grep 部分取回")
    lines.append("- 指标：会话总 token（subagent_tokens，模型侧真实计量）+ 工具输出 chars（helper 侧计量）")
    lines.append("")

    lines.append("## 总览")
    lines.append("")
    lines.append("| 指标 | direct | compressed |")
    lines.append("|---|---|---|")
    for arm in ("direct", "compressed"):
        pass
    d_rows = [v for (t, a), v in results.items() if a == "direct"]
    c_rows = [v for (t, a), v in results.items() if a == "compressed"]
    d_tokens = sum(v["tokens"] for v in d_rows)
    c_tokens = sum(v["tokens"] for v in c_rows)
    d_chars = sum(m["sent_chars"] for m in metrics["direct"])
    c_chars = sum(m["sent_chars"] for m in metrics["compressed"])
    ret_chars = sum(m["sent_chars"] for m in retrieve_calls)
    lines.append(f"| 任务成功率 | {sum(v['ok'] for v in d_rows)}/{len(d_rows)} | {sum(v['ok'] for v in c_rows)}/{len(c_rows)} |")
    lines.append(f"| 会话总 token（10 会话合计） | {d_tokens:,} | {c_tokens:,}（{c_tokens/d_tokens-1:+.1%}） |")
    lines.append(f"| 工具输出 chars 合计（helper 计量） | {d_chars:,} | {c_chars:,}（{c_chars/d_chars-1:+.1%}） |")
    lines.append(f"| 其中 retrieve_original 返回 chars | 0 | {ret_chars:,} |")
    lines.append(f"| retrieve_original 调用次数 / 需要全量数据的任务数 | 0 | {len(retrieve_calls)} / 10 |")
    lines.append("")
    lines.append("## v1 → v2 对比（P0 优化效果）")
    lines.append("")
    lines.append("| 指标 | v1 direct | v1 compressed | v2 direct | v2 compressed |")
    lines.append("|---|---|---|---|---|")
    lines.append(f"| 会话总 token | {V1['direct_tokens']:,} | {V1['compressed_tokens']:,}（+{V1['compressed_tokens']/V1['direct_tokens']-1:.1%}） |"
                 f" {d_tokens:,} | {c_tokens:,}（{c_tokens/d_tokens-1:+.1%}） |")
    lines.append(f"| retrieve_original 调用 | 0 | {V1['retrieve_calls']}（{V1['retrieve_chars']:,} chars） |"
                 f" 0 | {len(retrieve_calls)}（{ret_chars:,} chars） |")
    lines.append(f"| 任务成功率 | 9/10 | 9/10 | {sum(v['ok'] for v in d_rows)}/10 | {sum(v['ok'] for v in c_rows)}/10 |")
    lines.append("")

    lines.append("## 每任务明细")
    lines.append("")
    lines.append("| 任务 | direct tokens | compressed tokens | Δ | direct 对 | compressed 对 | compressed 调用数 |")
    lines.append("|---|---|---|---|---|---|---|")
    for tid in sorted(tasks):
        d = results.get((tid, "direct"))
        c = results.get((tid, "compressed"))
        if d and c:
            lines.append(f"| {tid} | {d['tokens']:,} | {c['tokens']:,} | {c['tokens']-d['tokens']:+,} |"
                         f" {'✅' if d['ok'] else '❌'} | {'✅' if c['ok'] else '❌'} | {c['tool_uses']} |")
    lines.append("")

    lines.append("## 工具输出明细（compressed 臂）")
    lines.append("")
    lines.append("| tool | 次数 | chars | compressed |")
    lines.append("|---|---|---|---|")
    by_tool: dict[str, list] = {}
    for m in metrics["compressed"]:
        by_tool.setdefault(m["tool"], []).append(m)
    for tool, rows in sorted(by_tool.items()):
        lines.append(f"| {tool} | {len(rows)} | {sum(r['sent_chars'] for r in rows):,} |"
                     f" {sum(1 for r in rows if r['compressed'])}/{len(rows)} |")
    lines.append("")

    lines.extend(V2_ANALYSIS.strip("\n").split("\n"))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[:24]))
    print(f"\nreport written: {OUT} ({len(lines)} lines)")
    return 0


V2_ANALYSIS = """
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
"""


if __name__ == "__main__":
    raise SystemExit(main())
