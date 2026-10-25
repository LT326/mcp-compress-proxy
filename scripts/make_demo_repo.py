"""创建 ZCode 接入演示用的固定小仓库 .e2e/demo-repo（幂等：已存在则跳过）。"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1] / ".e2e" / "demo-repo"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from zcode_entry import _patch_env  # noqa: E402

_patch_env()
GIT = shutil.which("git") or "git"


def run(*args: str) -> None:
    out = subprocess.run(
        [GIT, "-C", str(REPO), "-c", "core.autocrlf=false",
         "-c", "user.email=demo@local", "-c", "user.name=SW Demo", *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if out.returncode != 0:
        raise SystemExit(f"git {args} failed: {out.stderr}")


if REPO.exists():
    print(f"demo repo already exists: {REPO}")
    raise SystemExit(0)

REPO.mkdir(parents=True)
run("init", "-b", "main")
(REPO / "README.md").write_text(
    "# mcp-compress-proxy demo repo\n\n供 ZCode 接入 git-compressed MCP server 后调用工具验证。\n",
    encoding="utf-8", newline="\n",
)
run("add", "README.md")
run("commit", "-m", "demo: initial readme")
for i in range(1, 21):  # 20 commits：git_log 默认 max_count=10 → 约 60 行，必定触发代理压缩（head 30 + tail 10）
    (REPO / f"note_{i}.md").write_text(f"demo note {i}\n" + "padding line\n" * 30, encoding="utf-8", newline="\n")
    run("add", f"note_{i}.md")
    run("commit", "-m", f"demo: add note {i}")
print(f"demo repo created: {REPO} (21 commits)")
