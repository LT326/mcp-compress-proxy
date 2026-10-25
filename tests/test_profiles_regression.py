"""阶段四回归测试：每个 fixture × task_type 断言关键字段保留率 100%，
压缩率不低于 benchmark 报告（report.md）中对应行的下限。"""

import importlib.util
import json
from pathlib import Path

import pytest

BENCH = Path(__file__).parent / "benchmark" / "run_benchmark.py"
_spec = importlib.util.spec_from_file_location("run_benchmark", BENCH)
bm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bm)

CASES = {c["name"]: c for c in bm.build_cases()}
PROFILES = {name: bm.load_profile(name, bm.PROJECT_ROOT / "profiles") for name in ("git", "filesystem", "terminal")}

# report.md 明细表中每格压缩率的基础上留 ~5 个百分点的回归余量；
# terminal×unknown 刻意保守（fallback 首 50 + 尾 50 行），下限按实际放宽。
MIN_COMPRESSION = {
    ("git_log_debug.json", "debug"): 0.90,
    ("git_log_debug.json", "review"): 0.75,
    ("git_log_debug.json", "explore"): 0.93,
    ("git_log_debug.json", "unknown"): 0.80,
    ("git_log_review.json", "debug"): 0.92,
    ("git_log_review.json", "review"): 0.75,
    ("git_log_review.json", "explore"): 0.95,
    ("git_log_review.json", "unknown"): 0.85,
    ("git_diff_large.json", "debug"): 0.65,
    ("git_diff_large.json", "review"): 0.85,
    ("git_diff_large.json", "explore"): 0.90,
    ("git_diff_large.json", "unknown"): 0.90,
    ("fs_list_large.json", "debug"): 0.60,
    ("fs_list_large.json", "review"): 0.35,
    ("fs_list_large.json", "explore"): 0.54,
    ("fs_list_large.json", "unknown"): 0.95,
    ("fs_read_large.txt", "debug"): 0.98,
    ("fs_read_large.txt", "review"): 0.98,
    ("fs_read_large.txt", "explore"): 0.98,
    ("fs_read_large.txt", "unknown"): 0.98,
    ("terminal_test_output.txt", "debug"): 0.65,
    ("terminal_test_output.txt", "review"): 0.90,
    ("terminal_test_output.txt", "explore"): 0.80,
    ("terminal_test_output.txt", "unknown"): 0.15,
    ("terminal_build_log.txt", "debug"): 0.60,
    ("terminal_build_log.txt", "review"): 0.95,
    ("terminal_build_log.txt", "explore"): 0.88,
    ("terminal_build_log.txt", "unknown"): 0.15,
}

PARAMS = [(name, task) for name in CASES for task in CASES[name]["tasks"]]


@pytest.mark.parametrize("name,task", PARAMS)
def test_critical_retention_100pct_and_compression_floor(name, task, tmp_path):
    case = CASES[name]
    store = bm.SqliteStore(tmp_path / "refs.db")
    try:
        row = bm.run_case(case, task, PROFILES[case["server"]], store)
    finally:
        store.close()
    assert row["retention"] == 1.0, f"missed critical: {json.dumps(row['missed'], ensure_ascii=False)}"
    floor = MIN_COMPRESSION[(name, task)]
    assert row["compression"] >= floor, (
        f"{name} x {task}: compression {row['compression']:.1%} below floor {floor:.0%}"
    )


def test_overall_average_compression_meets_target(tmp_path):
    rows = []
    for name, case in CASES.items():
        store = bm.SqliteStore(tmp_path / f"{name}.db")
        try:
            for task in case["tasks"]:
                rows.append(bm.run_case(case, task, PROFILES[case["server"]], store))
        finally:
            store.close()
    avg = sum(r["compression"] for r in rows) / len(rows)
    assert avg >= 0.65, f"average compression {avg:.1%} below 65% target"
