"""真实会话基准：任务与 ground truth 生成（v2，merge-safe）。

设计原则：
- 全部任务用「git_log 从新到旧第 N 条」的列表位置语义（不受 merge 影响）；
- 每个任务在只允许 git_log/git_show/git_status、最多 8 次调用的约束下可解；
- 难度分层：头部可见（T7/T9）、中深定位（T1/T2/T4/T10）、深窗/全量（T3/T5/T6/T8，
  压缩臂的中段省略会逼出 retrieve_original 决策）。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
from zcode_entry import _patch_env  # noqa: E402

_patch_env()
GIT = shutil.which("git") or "git"
REPO = PROJECT_ROOT / ".e2e" / "real-bench" / "real-repo"
OUT = PROJECT_ROOT / ".e2e" / "real-bench" / "tasks.json"


def git(*args: str) -> str:
    out = subprocess.run([GIT, "-C", str(REPO), *args], capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    if out.returncode != 0:
        raise SystemExit(f"git {args} failed: {out.stderr}")
    return out.stdout.strip()


def git_raw(*args: str) -> str:
    out = subprocess.run([GIT, "-C", str(REPO), *args], capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    if out.returncode != 0:
        raise SystemExit(f"git {args} failed: {out.stderr}")
    return out.stdout.rstrip("\n")


def entries(n: int) -> list[tuple[str, str, str, str]]:
    lines = git("log", f"-{n}", "--format=%H|%s|%an|%ad", "--date=short").splitlines()
    return [tuple(line.split("|", 3)) for line in lines]


def prepare_workspace_state() -> None:
    tracked = git("ls-files").splitlines()
    target = next(p for p in tracked if p.endswith(".py"))
    file = REPO / target
    if "E2E-BENCH-MARKER" not in file.read_text(encoding="utf-8", errors="replace"):
        with open(file, "a", encoding="utf-8") as f:
            f.write("\n# E2E-BENCH-MARKER unstaged edit\n")
    staged = REPO / "docs" / "e2e_staged_note.txt"
    staged.write_text("staged by bench\n", encoding="utf-8")
    git("add", "--", "docs/e2e_staged_note.txt")


def main() -> int:
    print(f"repo commits: {git('rev-list', '--count', 'HEAD')}")

    e12, e30, e60 = entries(12), entries(30), entries(60)
    e5, e6, e9, e30_last = entries(5)[-1], e30[5], entries(9)[-1], e30[-1]

    # T4：第 k 条（5~15 内）需为非 merge 且 show 有文件列表
    t4_k, t4_entry, t4_files = None, None, None
    for k in range(5, 16):
        h = entries(k)[-1][0]
        if len(git("log", "-1", "--format=%P", h).split()) == 1:
            files = [l for l in git("show", "--name-only", "--format=", h).splitlines() if l.strip()]
            if files:
                t4_k, t4_files = k, sorted(files)
                break

    t5_count = sum(1 for _, s, _, _ in e60 if "fix" in s.lower())
    t6_authors = sorted({a for _, _, a, _ in e30})
    t7_hash = git("log", "-1", "-i", "--grep=typing", "--format=%H")
    t8 = " | ".join(f"{h[:7]}: {s}" for h, s, _, _ in entries(25))
    assert t7_hash in {h for h, _, _, _ in entries(10)}, "T7 答案必须头部可达"

    prepare_workspace_state()
    staged_list, unstaged_list = [], []
    for line in git_raw("status", "--porcelain").splitlines():
        x, y, path = line[0], line[1], line[3:].strip('"')
        if x in ("A", "M"):
            staged_list.append(path)
        if y == "M":
            unstaged_list.append(path)

    same = "same" if e5[2] == e9[2] else "different"

    tasks = [
        {"id": "T1", "prompt": "在 git_log（从新到旧）的第 12 条 commit：给出完整 40 位 hash，一个空格，然后 message 第一行。",
         "expected": f"{e12[-1][0]} {e12[-1][1]}", "checker": "exact"},
        {"id": "T2", "prompt": "在 git_log（从新到旧）的第 6 条 commit：给出完整 40 位 hash，作者名，日期(YYYY-MM-DD)，三者用单个空格分隔。",
         "expected": f"{e6[0]} {e6[2]} {e6[3]}", "checker": "exact"},
        {"id": "T3", "prompt": "查看最近 30 条 commit（git_log 从新到旧）：其中最旧的那一条（第 30 条）的完整 40 位 hash 是什么？",
         "expected": e30_last[0], "checker": "hash"},
        {"id": "T4", "prompt": f"在 git_log（从新到旧）的第 {t4_k} 条 commit 修改了哪些文件？给出所有文件的相对路径，按字母排序，用逗号分隔在一行。",
         "expected": ", ".join(t4_files), "checker": "set"},
        {"id": "T5", "prompt": "最近 60 条 commit 里，message 第一行包含 fix（不区分大小写，含 fixes/fixed 等）的有几条？只回答数字。",
         "expected": str(t5_count), "checker": "int"},
        {"id": "T6", "prompt": "最近 30 条 commit 里一共出现多少个不同的作者（按作者名去重）？只回答数字。",
         "expected": str(len(t6_authors)), "checker": "int"},
        {"id": "T7", "prompt": "message 中包含 typing（不区分大小写）的最近一条 commit 的完整 40 位 hash 是什么？",
         "expected": t7_hash, "checker": "hash"},
        {"id": "T8", "prompt": "列出最近 25 条 commit，每条格式 'hash前7位: message第一行'（冒号后一个空格），按从新到旧，全部放在一行，条目之间用 ' | ' 分隔。",
         "expected": t8, "checker": "lines"},
        {"id": "T9", "prompt": "当前工作区：哪些文件已暂存(staged)，哪些已修改但未暂存(unstaged)？回答格式 'staged: 文件…; unstaged: 文件…'（路径相对、各自按字母排序、多个用逗号分隔）。",
         "expected": f"staged: {', '.join(sorted(staged_list))}; unstaged: {', '.join(sorted(unstaged_list))}",
         "checker": "set"},
        {"id": "T10", "prompt": "第 5 条 commit 的作者和第 9 条 commit 的作者（git_log 从新到旧）是否为同一人？相同回答 same，不同回答 different；然后依次给出第 5 条作者名、第 9 条作者名，用逗号分隔。",
         "expected": f"{same}, {e5[2]}, {e9[2]}", "checker": "exact"},
    ]

    OUT.write_text(json.dumps(tasks, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {len(tasks)} tasks -> {OUT}")
    for t in tasks:
        exp = t["expected"]
        print(f"  {t['id']} [{t['checker']}] {exp[:100]}{'...' if len(exp) > 100 else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
