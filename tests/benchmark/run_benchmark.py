"""压缩基准（阶段四）。

运行：python tests/benchmark/run_benchmark.py
- 自动生成缺失的 synthetic fixtures（tests/fixtures/gen_fixtures.py）
- fixtures × task_types → 四个指标：
    1. 输入 token 压缩率      1 - compressed_chars / original_chars
    2. 关键字段保留率          critical paths 值与原值一致（或 contains 命中）
    3. retrieve_original 调用率 人工标注（脚本无法自动判定时）+ 保留率未达标自动视为需取回
    4. 端到端净值             (compressed_chars + retrieve_chars) / original_chars
- 摘要打印 stdout，完整结果写入 tests/benchmark/report.md
"""

from __future__ import annotations

import importlib.util
import json
import statistics
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parents[1]
FIXTURES = HERE.parent / "fixtures"
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from mcp_compress_proxy.compressor import paths  # noqa: E402
from mcp_compress_proxy.compressor.engine import compress  # noqa: E402
from mcp_compress_proxy.profiles.loader import Profile, load_profile  # noqa: E402
from mcp_compress_proxy.store.sqlite_store import SqliteStore  # noqa: E402
from mcp_compress_proxy.task_context.detector import TaskType  # noqa: E402


# ---------------------------------------------------------------- fixtures 加载

def ensure_fixtures() -> None:
    gen = HERE.parent / "fixtures" / "gen_fixtures.py"
    spec = importlib.util.spec_from_file_location("gen_fixtures", gen)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.main(force=False)


def load_terminal(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    command = lines[0][2:] if lines[0].startswith("$ ") else ""
    stderr_idx = lines.index("===== STDERR =====")
    exit_idx = lines.index(next(l for l in lines if l.startswith("===== EXIT CODE: ")))
    exit_code = int(lines[exit_idx].split("EXIT CODE:")[1].replace("=", "").strip())
    return {
        "command": command,
        "stdout": "\n".join(lines[1:stderr_idx]),
        "stderr": "\n".join(lines[stderr_idx + 1 : exit_idx]),
        "exit_code": exit_code,
    }


def _first_line(payload: dict, i: int) -> str:
    return payload["commits"][i]["message"].splitlines()[0]


# ---------------------------------------------------------------- 用例定义

def build_cases() -> list[dict]:
    ensure_fixtures()
    cases: list[dict] = []

    for name, n in (("git_log_debug.json", 20), ("git_log_review.json", 30)):
        payload = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
        last = n - 1
        cases.append(
            {
                "name": name,
                "server": "git",
                "source": "synthetic",
                "payload": payload,
                "tasks": ["debug", "review", "explore", "unknown"],
                "critical": {
                    "debug": [
                        {"path": "repository"},
                        {"path": "total"},
                        {"path": "commits[0].hash"},
                        {"path": "commits[0].message"},
                        {"path": "commits[2].message"},
                        {"path": "commits[2].diff_stat"},
                    ],
                    "review": [
                        {"path": "total"},
                        {"path": "commits[0].hash"},
                        {"path": f"commits[{last}].hash"},
                        {"path": f"commits[{last}].author"},
                        {"path": f"commits[{last}].date"},
                        {"path": "commits[7].message", "contains": _first_line(payload, 7)},
                    ],
                    "explore": [
                        {"path": "total"},
                        {"path": "commits[4].hash"},
                        {"path": "commits[4].message", "contains": _first_line(payload, 4)},
                    ],
                    "unknown": [{"path": "repository"}, {"path": "commits[2].hash"}],
                },
                "retrieve_notes": {
                    "debug": (False, "最近 3 条 commit 完整信息 + diff_stat 足以定位最近引入的缺陷"),
                    "review": (False, "hash/author/date + message 首行满足变更清单评审"),
                    "explore": (False, "最近 5 条 hash + 首行满足仓库结构摸底"),
                    "unknown": (False, "声明的 fallback 保留顶层键与最近 3 条完整 commit"),
                },
            }
        )

    diff_payload = json.loads((FIXTURES / "git_diff_large.json").read_text(encoding="utf-8"))
    cases.append(
        {
            "name": "git_diff_large.json",
            "server": "git",
            "source": "synthetic",
            "payload": diff_payload,
            "tasks": ["debug", "review", "explore", "unknown"],
            "critical": {
                "debug": [
                    {"path": "base"},
                    {"path": "files[0].diff"},
                    {"path": "files[2].diff"},
                    {"path": "files[14].path"},
                    {"path": "files[14].status"},
                ],
                "review": [
                    {"path": "files[14].path"},
                    {"path": "files[14].additions"},
                    {"path": "files[14].deletions"},
                ],
                "explore": [{"path": "files[9].path"}],
                "unknown": [{"path": "base"}, {"path": "files[0].path"}, {"path": "summary"}],
            },
            "retrieve_notes": {
                "debug": (False, "全部文件路径 + 前 3 个文件完整 hunk 满足定点修复"),
                "review": (False, "逐文件 +/- 统计满足评审概览；看具体代码时按需取回属正常交互"),
                "explore": (False, "文件路径清单满足变更面摸底"),
                "unknown": (False, "fallback 保留 base/head/summary + 文件路径"),
            },
        }
    )

    fs_list_payload = json.loads((FIXTURES / "fs_list_large.json").read_text(encoding="utf-8"))
    cases.append(
        {
            "name": "fs_list_large.json",
            "server": "filesystem",
            "source": "synthetic",
            "payload": fs_list_payload,
            "tasks": ["debug", "review", "explore", "unknown"],
            "critical": {
                "debug": [{"path": "path"}, {"path": "entries[199].path"}],
                "review": [
                    {"path": "entries[199].path"},
                    {"path": "entries[199].size"},
                    {"path": "entries[199].mtime"},
                ],
                "explore": [{"path": "entries[100].path"}, {"path": "entries[100].size"}],
                "unknown": [{"path": "path"}, {"path": "entries[2].path"}],
            },
            "retrieve_notes": {
                "debug": (False, "目录路径 + 全部条目路径足以导航定位"),
                "review": (False, "path/size/mtime 全量保留，无需取回"),
                "explore": (False, "路径 + 大小满足结构概览"),
                "unknown": (False, "fallback 保留目录级键与前 3 条完整条目"),
            },
        }
    )

    read_text = (FIXTURES / "fs_read_large.txt").read_text(encoding="utf-8")
    fs_read_payload = {
        "path": "src/big_module.py",
        "size": len(read_text),
        "mtime": "2026-10-20T11:00:00",
        "content": read_text.rstrip("\n"),
    }
    cases.append(
        {
            "name": "fs_read_large.txt",
            "server": "filesystem",
            "source": "synthetic",
            "payload": fs_read_payload,
            "tasks": ["debug", "review", "explore", "unknown"],
            "critical": {
                "debug": [
                    {"path": "path"},
                    {"path": "content", "contains": "unexpected state in phase transition"},
                    {"path": "content", "contains": "# error path: invalid config"},
                ],
                "review": [{"path": "path"}, {"path": "size"}],
                "explore": [{"path": "path"}],
                "unknown": [{"path": "path"}, {"path": "size"}],
            },
            "retrieve_notes": {
                "debug": (False, "错误锚点行 ±3 上下文即调试所需；正文其余部分按需取回属正常交互"),
                "review": (True, "代码内容评审需要文件正文；v1.0 profile 按规格 drop content，必须取回 —— 已知张力，见报告 v1.1 建议"),
                "explore": (False, "路径 + 元数据满足仓库摸底"),
                "unknown": (False, "声明的 fallback 保留元数据、丢正文"),
            },
        }
    )

    for name in ("terminal_test_output.txt", "terminal_build_log.txt"):
        payload = load_terminal(FIXTURES / name)
        is_test = "test" in name
        critical = {
            "debug": (
                [
                    {"path": "exit_code"},
                    {"path": "stdout", "contains": "AssertionError: expected 42, got 41"},
                    {"path": "stdout", "contains": "FAILED tests/test_engine.py::test_parse"},
                ]
                if is_test
                else [
                    {"path": "exit_code"},
                    {"path": "stdout", "contains": "✓ built in 4.21s"},
                    {"path": "stdout", "contains": "building for production"},
                ]
            ),
            "review": (
                [
                    {"path": "exit_code"},
                    {"path": "stdout", "contains": "FAILED tests/test_engine.py::test_parse"},
                    {"path": "stdout", "contains": "short test summary"},
                ]
                if is_test
                else [{"path": "exit_code"}, {"path": "command"}]
            ),
            "explore": (
                [
                    {"path": "exit_code"},
                    {"path": "stdout", "contains": "Traceback"},
                ]
                if is_test
                else [
                    {"path": "exit_code"},
                    {"path": "stdout", "contains": "building for production"},
                ]
            ),
            "unknown": [{"path": "exit_code"}, {"path": "command"}],
        }
        notes = (
            {
                "debug": (False, "退出码 + 首尾 40 行 + R5 错误块覆盖失败上下文"),
                "review": (False, "失败测试名 + 摘要 + R5 错误块即评审结论所需"),
                "explore": (False, "退出码 + 头 10 行 + R5 强制保留的 Traceback 块"),
                "unknown": (False, "fallback 首 50 + 尾 50 行"),
            }
            if is_test
            else {
                "debug": (False, "构建成功无错误信号，退出码 + 首尾日志足够"),
                "review": (False, "无 FAILED/ERROR 行 → stdout 全丢，退出码即结论"),
                "explore": (False, "退出码 + 头部构建信息足够判断构建状态"),
                "unknown": (False, "fallback 首 50 + 尾 50 行"),
            }
        )
        cases.append(
            {
                "name": name,
                "server": "terminal",
                "source": "synthetic",
                "payload": payload,
                "tasks": ["debug", "review", "explore", "unknown"],
                "critical": critical,
                "retrieve_notes": notes,
            }
        )

    return cases


# ---------------------------------------------------------------- 指标计算

def chars(obj) -> int:
    return len(json.dumps(obj, ensure_ascii=False))


def check_critical(payload: dict, compressed_payload: dict, specs: list[dict]) -> list[dict]:
    missed = []
    for spec in specs:
        comp = paths.extract(compressed_payload, spec["path"])
        if "contains" in spec:
            ok = bool(comp) and isinstance(comp[0][1], str) and spec["contains"] in comp[0][1]
        else:
            orig = paths.extract(payload, spec["path"])
            ok = bool(orig) and bool(comp) and comp[0][1] == orig[0][1]
        if not ok:
            missed.append(spec)
    return missed


def run_case(case: dict, task: str, profile: Profile, store: SqliteStore) -> dict:
    payload = case["payload"]
    result = compress(payload, profile, task, store=store)
    original_chars = chars(payload)
    compressed_chars = chars(result.compressed_payload)

    specs = case["critical"].get(task, [])
    missed = check_critical(payload, result.compressed_payload, specs)
    retention = 1.0 if not specs else 1 - len(missed) / len(specs)

    note_retrieved, note_reason = case["retrieve_notes"].get(task, (False, ""))
    retrieved = note_retrieved or retention < 1.0
    retrieve_chars = original_chars if retrieved else 0

    return {
        "case": case["name"],
        "server": case["server"],
        "task": task,
        "original_chars": original_chars,
        "compressed_chars": compressed_chars,
        "compression": 1 - compressed_chars / original_chars,
        "retention": retention,
        "missed": missed,
        "retrieved": retrieved,
        "retrieve_reason": note_reason,
        "net_ratio": (compressed_chars + retrieve_chars) / original_chars,
    }


def generic_fallback_ratio(case: dict) -> float:
    """通用 fallback 基线：空 profile + unknown → 顶层键 + 数组前 3 项。"""
    with tempfile.TemporaryDirectory() as tmp:
        store = SqliteStore(Path(tmp) / "refs.db")
        result = compress(case["payload"], Profile(server=case["server"]), TaskType.UNKNOWN, store=store)
        store.close()
    return 1 - chars(result.compressed_payload) / chars(case["payload"])


# ---------------------------------------------------------------- 主流程

TARGET_COMPRESSION = 0.65
TARGET_RETENTION = 1.0
TARGET_RETRIEVE = 0.15


def main() -> int:
    cases = build_cases()
    profiles = {name: load_profile(name, PROJECT_ROOT / "profiles") for name in ("git", "filesystem", "terminal")}

    rows: list[dict] = []
    with tempfile.TemporaryDirectory() as tmp:
        store = SqliteStore(Path(tmp) / "bench.db")
        for case in cases:
            profile = profiles[case["server"]]
            for task in case["tasks"]:
                rows.append(run_case(case, task, profile, store))
        store.close()  # Windows：必须先关句柄，临时目录才能删除

    avg_compression = statistics.mean(r["compression"] for r in rows)
    min_retention = min(r["retention"] for r in rows)
    retrieve_rate = sum(1 for r in rows if r["retrieved"]) / len(rows)
    avg_net = statistics.mean(r["net_ratio"] for r in rows)

    print(f"cases: {len(rows)} (7 fixtures x 4 task types)")
    print(f"{'fixture':28} {'task':8} {'compress':>9} {'retain':>7} {'retr':>5} {'net':>6}")
    for r in rows:
        print(
            f"{r['case']:28} {r['task']:8} {r['compression']:8.1%} {r['retention']:7.0%}"
            f" {'yes' if r['retrieved'] else 'no':>5} {r['net_ratio']:6.2f}"
        )
    print("-" * 70)
    print(f"avg compression : {avg_compression:.1%}  (target >= {TARGET_COMPRESSION:.0%})")
    print(f"min retention   : {min_retention:.0%}  (target {TARGET_RETENTION:.0%})")
    print(f"retrieve rate   : {retrieve_rate:.1%}  (target < {TARGET_RETRIEVE:.0%})")
    print(f"avg net ratio   : {avg_net:.2f}  (compressed+retrieve vs original)")

    report = render_report(rows, cases, avg_compression, min_retention, retrieve_rate, avg_net)
    report_path = HERE / "report.md"
    report_path.write_text(report, encoding="utf-8")
    print(f"\nreport written: {report_path}")

    ok = (
        avg_compression >= TARGET_COMPRESSION
        and min_retention >= TARGET_RETENTION
        and retrieve_rate < TARGET_RETRIEVE
    )
    print("targets:", "ALL PASS" if ok else "NOT MET")
    return 0 if ok else 1


def render_report(rows, cases, avg_compression, min_retention, retrieve_rate, avg_net) -> str:
    by_server: dict[str, list[dict]] = {}
    for r in rows:
        by_server.setdefault(r["server"], []).append(r)

    lines: list[str] = []
    lines.append("# 压缩基准报告 report.md（v1.0）")
    lines.append("")
    lines.append(f"- 生成时间：{datetime.now(timezone.utc).isoformat()}")
    lines.append("- fixtures：7 份，全部 **synthetic**（`tests/fixtures/gen_fixtures.py` 固定种子 20261024 生成，无 real 样本）")
    lines.append("- 指标定义：压缩率 = 1 - compressed_chars/original_chars；保留率 = critical paths 值一致比例；")
    lines.append("  retrieve 率 = 需要 retrieve_original 的组合占比（人工标注 + 保留率未达标自动计入）；")
    lines.append("  net = (compressed_chars + retrieve_chars) / original_chars")
    lines.append("")
    lines.append("## 目标达成")
    lines.append("")
    lines.append("| 指标 | 目标 | 实际 | 结果 |")
    lines.append("|---|---|---|---|")
    lines.append(f"| 平均压缩率 | >= 65% | {avg_compression:.1%} | {'PASS' if avg_compression >= TARGET_COMPRESSION else 'FAIL'} |")
    lines.append(f"| 关键字段保留率（最低） | 100% | {min_retention:.0%} | {'PASS' if min_retention >= TARGET_RETENTION else 'FAIL'} |")
    lines.append(f"| retrieve_original 调用率 | < 15% | {retrieve_rate:.1%} | {'PASS' if retrieve_rate < TARGET_RETRIEVE else 'FAIL'} |")
    lines.append(f"| 平均端到端净值 | 越低越好 | {avg_net:.2f} | - |")
    lines.append("")

    lines.append("## 分 server 汇总")
    lines.append("")
    lines.append("| server | 组合数 | 平均压缩率 | 最低保留率 | retrieve 数 |")
    lines.append("|---|---|---|---|---|")
    for server, group in by_server.items():
        lines.append(
            f"| {server} | {len(group)} | {statistics.mean(r['compression'] for r in group):.1%} "
            f"| {min(r['retention'] for r in group):.0%} "
            f"| {sum(1 for r in group if r['retrieved'])} |"
        )
    lines.append("")

    lines.append("## 明细（fixture × task_type）")
    lines.append("")
    lines.append("| fixture | task | 压缩率 | 保留率 | retrieved | net | 原始 chars | 压缩 chars |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for r in rows:
        lines.append(
            f"| {r['case']} | {r['task']} | {r['compression']:.1%} | {r['retention']:.0%} "
            f"| {'**yes**' if r['retrieved'] else 'no'} | {r['net_ratio']:.2f} "
            f"| {r['original_chars']} | {r['compressed_chars']} |"
        )
    lines.append("")

    lines.append("## retrieve_original 标注明细")
    lines.append("")
    for r in rows:
        if r["retrieved"]:
            reason = r["retrieve_reason"] or ("保留率未达标：" + json.dumps(r["missed"], ensure_ascii=False))
            lines.append(f"- **{r['case']} × {r['task']}**：{reason}")
    lines.append("")

    lines.append("## 失败案例（保留率 <100% 或压缩率 <40%）")
    lines.append("")
    failures = [r for r in rows if r["retention"] < 1.0 or r["compression"] < 0.40]
    if failures:
        for r in failures:
            detail = json.dumps(r["missed"], ensure_ascii=False) if r["missed"] else "-"
            lines.append(f"- {r['case']} × {r['task']}：retention={r['retention']:.0%}, compression={r['compression']:.1%}, missed={detail}")
    else:
        lines.append("（无）")
    lines.append("")

    lines.append("## 与基线对比（平均压缩率）")
    lines.append("")
    lines.append("| 方案 | 平均压缩率 |")
    lines.append("|---|---|")
    lines.append("| 无压缩基线 | 0.0% |")
    generic = statistics.mean(generic_fallback_ratio(c) for c in cases)
    lines.append(f"| 通用 fallback（顶层键 + 数组前 3 项） | {generic:.1%} |")
    lines.append(f"| **任务感知 Profile（本方案）** | **{avg_compression:.1%}** |")
    lines.append("")

    lines.append("## 已知边界与 v1.1 建议")
    lines.append("")
    lines.append("1. **fs read × review 张力**：规格要求 review drop content，但代码内容评审必须看正文 →")
    lines.append("   该组合 retrieve=yes，net=1.00 反而高于原始。建议 v1.1 给 filesystem review 增加")
    lines.append("   `content: first_line` 或 diff-aware 保留。")
    lines.append("2. **代理上下文弱信号**：代理只能从 tools/call 参数提取文本（AGENTS.md R9），真实链路中")
    lines.append("   unknown → 声明 fallback 的触发率会高于本基准（本基准直接指定 task_type）。")
    lines.append("3. **MCP initialize 不含工具列表**：注入点实际是 tools/list（initialize 已做防御式兼容）。")
    lines.append("4. 通用 fallback 对「大字符串字段」无压缩能力（顶层键整保留）——已被三个 server 的")
    lines.append("   声明式 fallback 覆盖，但接入第四个 server 时必须先写声明 fallback。")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
