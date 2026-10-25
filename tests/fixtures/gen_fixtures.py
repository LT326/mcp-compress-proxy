"""生成 benchmark fixtures（全部 synthetic，固定种子 20261024，可复现）。

运行：python tests/fixtures/gen_fixtures.py [--force]
- git_log_debug.json      20 条 commit 的 git log --stat 输出（含 diff）
- git_log_review.json     30 条 commit 的 git log 输出
- git_diff_large.json     15 个文件变更的 git diff 输出
- fs_list_large.json      递归 list_dir 的 200 个条目
- fs_read_large.txt       3000 行源码文件读取结果
- terminal_test_output.txt  失败 pytest 运行的完整 stdout + stderr
- terminal_build_log.txt  npm build 完整日志
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

GIT_SUBJECTS = [
    "fix parser edge case in diff hunk splitting",
    "refactor compressor to whitelist semantics",
    "add regression tests for profiles",
    "update documentation for phase four",
    "handle empty payload in fallback strategy",
    "optimize path expansion for large arrays",
    "rename store module internals",
    "bump version and refresh changelog",
]


def git_commit(i: int) -> dict:
    subject = GIT_SUBJECTS[i % len(GIT_SUBJECTS)]
    body = "\n".join(f"  - detail {j}: " + "explanation padding text " * 3 for j in range(2))
    return {
        "hash": f"{i:04x}3f9a" + "b" * 34,
        "author": f"dev{i % 5} <dev{i % 5}@example.com>",
        "date": f"2026-10-{i % 28 + 1:02d}T09:{i % 60:02d}:00+08:00",
        "message": f"{subject} (#{1000 + i})\n{body}",
        "diff_stat": f" {i % 9 + 1} files changed, {i * 7 % 90 + 5} insertions(+), {i * 3 % 40} deletions(-)",
        "diff": "\n".join(
            f"@@ -{k * 10},{i % 20 + 1} +{k * 10},{i % 20 + 1} @@ def module_{i}\n"
            f"- old implementation line {k} with some content\n"
            f"+ new implementation line {k} with different content padding"
            for k in range(4)
        ),
        "tree": "t" * 40,
        "parent_hashes": ["p" * 40] if i == 0 else ["p" * 40, "q" * 40],
    }


def git_log(n: int) -> dict:
    return {
        "repository": "demo/repo",
        "total": n,
        "commits": [git_commit(i) for i in range(n)],
    }


def git_diff(n: int = 15) -> dict:
    return {
        "repository": "demo/repo",
        "base": "main",
        "head": "feature/phase4",
        "summary": f"{n} files changed, 345 insertions(+), 87 deletions(-)",
        "total_files": n,
        "files": [
            {
                "path": f"src/mcp_compress_proxy/module_{i}/part_{i}_{i * 3}.py",
                "status": "modified" if i % 3 else "added",
                "additions": 20 + i * 7,
                "deletions": i * 3,
                "diff": "\n".join(
                    f"@@ -{k * 12},{30 + i} +{k * 12},{30 + i} @@ class Handler{i}\n"
                    f"     context line stays here\n"
                    f"- removed line {k} with original logic padding text here\n"
                    f"+ added line {k} with replacement logic and more padding"
                    for k in range(6)
                ),
            }
            for i in range(n)
        ],
    }


def fs_list(n: int = 200) -> dict:
    return {
        "path": "src",
        "error": "",
        "truncated": False,
        "entries": [
            {
                "path": f"src/module_{i % 17}/component_{i}/file_{i}_part.py",
                "name": f"file_{i}_part.py",
                "type": "dir" if i % 4 == 0 else "file",
                "size": 120 + i * 37,
                "mtime": f"2026-10-{i % 28 + 1:02d}T08:00:{i % 60:02d}",
                "permissions": "rwxr-xr-x" if i % 3 == 0 else "rw-r--r--",
            }
            for i in range(n)
        ],
    }


def fs_read_lines(n: int = 3000) -> list[str]:
    lines: list[str] = []
    for i in range(n):
        if i == 500:
            lines.append('    raise RuntimeError("unexpected state in phase transition")')
        elif i == 2200:
            lines.append("    except ValueError as exc:  # error path: invalid config")
        elif i % 50 == 0:
            lines.append(f"def handler_{i}(request, context):")
        elif i % 50 == 1:
            lines.append(f'    """Handler {i} docstring with explanation padding."""')
        else:
            lines.append(f"    value_{i % 50} = transform(payload_{i % 7}, offset={i})")
    return lines


def pytest_stdout() -> list[str]:
    lines = [
        f"tests/test_module_{i % 6}.py::test_case_{i} PASSED [ {i * 3 + 2:3d}%]"
        for i in range(120)
    ]
    lines += [
        "",
        "=================================== FAILURES ===================================",
        "_________________________________ test_parse ___________________________________",
        "",
        "Traceback (most recent call last):",
        '  File "tests/test_engine.py", line 42, in test_parse',
        "    assert parse(text) == expected",
        "AssertionError: expected 42, got 41",
        "",
        "=========================== short test summary info ============================",
        "FAILED tests/test_engine.py::test_parse - AssertionError: expected 42, got 41",
        "1 failed, 119 passed in 6.78s",
    ]
    return lines


PYTEST_STDERR = [
    f"PytestDeprecationWarning: hook impl {name} uses old-style configuration"
    for name in ("report_header", "collect_file", "addoption_wrap")
]


def npm_build_stdout() -> list[str]:
    lines = ["> app@0.1.0 build", "> vite build", "", "vite v5.4.11 building for production..."]
    lines += [f"transforming ({i}) src/components/Item{i:03d}.vue" for i in range(100)]
    lines += ["✓ 148 modules transformed.", "rendering chunks (24)...", "computing gzip size (0.12 s)"]
    lines += [f"dist/assets/chunk-{i:02d}-a1b2c3d4.js  {123.4 + i * 2.3:7.2f} kB │ gzip: {45.6 + i:5.2f} kB" for i in range(20)]
    lines += ["✓ built in 4.21s"]
    return lines


def write_terminal(path: Path, command: str, stdout: list[str], stderr: list[str], exit_code: int) -> None:
    text = "\n".join([f"$ {command}"] + stdout + ["===== STDERR ====="] + stderr + [f"===== EXIT CODE: {exit_code} ====="])
    path.write_text(text + "\n", encoding="utf-8")


def main(force: bool = False) -> None:
    outputs = {
        "git_log_debug.json": lambda p: p.write_text(
            json.dumps(git_log(20), ensure_ascii=False, indent=1), encoding="utf-8"
        ),
        "git_log_review.json": lambda p: p.write_text(
            json.dumps(git_log(30), ensure_ascii=False, indent=1), encoding="utf-8"
        ),
        "git_diff_large.json": lambda p: p.write_text(
            json.dumps(git_diff(), ensure_ascii=False, indent=1), encoding="utf-8"
        ),
        "fs_list_large.json": lambda p: p.write_text(
            json.dumps(fs_list(), ensure_ascii=False, indent=1), encoding="utf-8"
        ),
        "fs_read_large.txt": lambda p: p.write_text(
            "\n".join(fs_read_lines()) + "\n", encoding="utf-8"
        ),
        "terminal_test_output.txt": lambda p: write_terminal(
            p, "python -m pytest tests/ -v", pytest_stdout(), PYTEST_STDERR, 1
        ),
        "terminal_build_log.txt": lambda p: write_terminal(
            p, "npm run build", npm_build_stdout(), [], 0
        ),
    }
    for name, writer in outputs.items():
        path = HERE / name
        if path.exists() and not force:
            continue
        writer(path)
        print(f"wrote {path.name}")


if __name__ == "__main__":
    main(force="--force" in sys.argv)
