"""ZCode → 压缩代理 的启动入口：修复 GUI 拉起时环境变量不完整的问题。

ZCode 主进程的 PATH 极简（实测 spawn powershell.exe/reg.exe 都 ENOENT），
stdio MCP 子进程继承该环境，导致 mcp-server-git 内的 GitPython 在 import 时
报 "Bad git executable" 直接崩溃（连接超时的根因）。

本入口在启动代理前完成三件事，之后参数原样交给 mcp_compress_proxy.proxy：
1. 把注册表（HKCU/HKLM）PATH 里的目录合并进 os.environ（git/uvx 装在那里）；
2. 解析 git.exe 绝对路径写入 GIT_PYTHON_GIT_EXECUTABLE（GitPython 官方逃生口）；
3. 补齐 TEMP/TMP/USERPROFILE 等基础变量（防环境被整体替换）。

ZCode 配置用法：
  command: E:\\GLM\\mcp-compress-proxy\\.venv\\Scripts\\python.exe
  args: [this_script, --server, git, --db, <db>, --,
         C:\\Users\\a\\.local\\bin\\mcp-server-git.exe, --repository, <repo>]
"""

from __future__ import annotations

import os
import shutil
import sys


def _registry_path_entries() -> list[str]:
    entries: list[str] = []
    try:
        import winreg
    except ImportError:
        return entries
    for hive, subkey in (
        (winreg.HKEY_CURRENT_USER, "Environment"),
        (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
    ):
        try:
            with winreg.OpenKey(hive, subkey) as key:
                value, _ = winreg.QueryValueEx(key, "Path")
                entries.extend(p for p in value.split(os.pathsep) if p and not p.startswith("%"))
        except OSError:
            pass
    return entries


def _patch_env() -> None:
    existing = os.environ.get("PATH", "")
    seen = {p.rstrip("\\").lower() for p in existing.split(os.pathsep) if p}
    additions = []
    for d in _registry_path_entries():
        key = d.rstrip("\\").lower()
        if key not in seen:
            seen.add(key)
            additions.append(d)
    if additions:
        os.environ["PATH"] = os.pathsep.join(additions + ([existing] if existing else []))

    git = shutil.which("git")
    if git and not os.environ.get("GIT_PYTHON_GIT_EXECUTABLE"):
        os.environ["GIT_PYTHON_GIT_EXECUTABLE"] = git

    home = os.path.expanduser("~")
    os.environ.setdefault("USERPROFILE", home)
    os.environ.setdefault("TEMP", os.path.join(home, "AppData", "Local", "Temp"))
    os.environ.setdefault("TMP", os.environ.get("TEMP"))
    os.environ.setdefault("SYSTEMROOT", r"C:\Windows")


def main() -> None:
    _patch_env()
    from mcp_compress_proxy.proxy.main import main as proxy_main

    proxy_main(sys.argv[1:])


if __name__ == "__main__":
    main()
