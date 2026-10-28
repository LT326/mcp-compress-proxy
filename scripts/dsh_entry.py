"""DeepSeek Harness → 压缩代理 的启动入口。

和 scripts/zcode_entry.py 同源：DSH（Electron GUI）拉起 stdio MCP 子进程时，
环境可能缺少 git / powershell 等 PATH 条目。这里先做同样的环境修复，再把参数
原样交给 mcp_compress_proxy.proxy：

  command: <python>
  args: [<repo>/scripts/dsh_entry.py, --server, git, --db, <db>,
         --profiles-dir, <repo>/profiles, --, <server 命令>, ...]

单独运行时等价于：
  python -m mcp_compress_proxy.proxy --server git ... -- <server 命令> ...
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def _ensure_script_dir_on_path() -> None:
    here = Path(__file__).resolve().parent
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))


def _ensure_github_token(argv: list[str]) -> None:
    """GitHub server 需要 GITHUB_PERSONAL_ACCESS_TOKEN；DSH 会滤掉环境里的 TOKEN 变量。

    没显式配置时，从本机 gh CLI 的登录态取一次（不落盘、不写进配置）。
    """
    if "--server" not in argv:
        return
    index = argv.index("--server")
    if index + 1 >= len(argv) or argv[index + 1] != "github":
        return
    if os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN"):
        return
    if os.environ.get("GITHUB_TOKEN"):
        os.environ["GITHUB_PERSONAL_ACCESS_TOKEN"] = os.environ["GITHUB_TOKEN"]
        return
    gh = shutil.which("gh")
    if not gh:
        return
    try:
        proc = subprocess.run(
            [gh, "auth", "token"], capture_output=True, text=True, timeout=15
        )
    except (OSError, subprocess.TimeoutExpired):
        return
    token = proc.stdout.strip()
    if proc.returncode == 0 and token:
        os.environ["GITHUB_PERSONAL_ACCESS_TOKEN"] = token


def main() -> None:
    _ensure_script_dir_on_path()
    from zcode_entry import _patch_env  # 注册表 PATH / GIT_PYTHON_GIT_EXECUTABLE / TEMP

    _patch_env()
    argv = sys.argv[1:]
    _ensure_github_token(argv)
    from mcp_compress_proxy.proxy.main import main as proxy_main

    proxy_main(argv)


if __name__ == "__main__":
    main()