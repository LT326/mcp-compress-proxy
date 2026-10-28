"""发布 mcp-compress-proxy 到 PyPI + npm，并打印插件市场条目。

默认 dry-run：只做构建与校验，不写任何 registry。真正发布需要：
  - PyPI：环境变量 UV_PUBLISH_TOKEN（或 uv 已配置的 token）
  - npm：本机已 npm login（npm whoami 能返回账号）

用法：
  .venv\\Scripts\\python.exe scripts/publish_release.py --check
  .venv\\Scripts\\python.exe scripts/publish_release.py --yes
  .venv\\Scripts\\python.exe scripts/publish_release.py --yes --skip-pypi
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / ".e2e" / "dist"
PLUGIN_DIR = ROOT / "integrations" / "dsh"
MARKET_ENTRY = (
    "- [LT326/mcp-compress-proxy](https://github.com/LT326/mcp-compress-proxy) - "
    "Reversible compression proxy for MCP tool output: git, GitHub issue/PR/commit "
    "lists, filesystem reads, memory graphs and SQLite queries shrink 25-99%, with an "
    "injected retrieve_original tool for exact recovery."
)


def _exe(name: str) -> str:
    for candidate in (name, f"{name}.cmd", f"{name}.exe", f"{name}.CMD"):
        found = shutil.which(candidate)
        if found:
            return found
    return name


def run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    resolved = [_exe(cmd[0]), *cmd[1:]]
    return subprocess.run(resolved, cwd=cwd, capture_output=True, text=True)


def versions() -> tuple[str, str]:
    py = re.search(r'^version\s*=\s*"([^"]+)"', (ROOT / "pyproject.toml").read_text("utf-8"), re.M)
    pkg = json.loads((PLUGIN_DIR / "package.json").read_text("utf-8"))
    assert py, "pyproject.toml 缺少 version"
    return py.group(1), pkg["version"]


def build() -> Path:
    DIST.mkdir(parents=True, exist_ok=True)
    for old in DIST.glob("mcp_compress_proxy-*"):
        old.unlink()
    proc = run(["uv", "build", "--out-dir", str(DIST)], cwd=ROOT)
    if proc.returncode != 0:
        raise SystemExit(f"uv build failed:\n{proc.stderr[-1500:]}")
    wheel = next(DIST.glob("*.whl"))
    with zipfile.ZipFile(wheel) as zf:
        profiles = [n for n in zf.namelist() if n.endswith(".yaml") and "share/mcp-compress-proxy/profiles" in n]
    if len(profiles) < 6:
        raise SystemExit(f"wheel 里只有 {len(profiles)} 个 profile（应为 >=6）：{profiles}")
    print(f"[build] {wheel.name}: {len(profiles)} profiles packaged")
    return wheel


def npm_pack_check() -> None:
    proc = run(["npm", "pack", "--dry-run", "--json"], cwd=PLUGIN_DIR)
    if proc.returncode != 0:
        raise SystemExit(f"npm pack failed:\n{proc.stderr[-1500:]}")
    data = json.loads(proc.stdout)
    files = {f["path"] for f in data[0]["files"]}
    required = {"package.json", "index.js", "cordis.patch.yml", "README.md"}
    missing = required - files
    if missing:
        raise SystemExit(f"npm 包缺少文件: {sorted(missing)}")
    print(f"[npm] {data[0]['name']}@{data[0]['version']}: {len(files)} files, "
          f"{data[0]['size']} bytes packed")


def pypi_ready() -> bool:
    return bool(os.environ.get("UV_PUBLISH_TOKEN") or os.environ.get("TWINE_PASSWORD"))


def npm_ready() -> bool:
    proc = run(["npm", "whoami"])
    return proc.returncode == 0


def publish_pypi() -> None:
    proc = run(["uv", "publish", str(DIST)])
    if proc.returncode != 0:
        raise SystemExit(f"PyPI publish failed:\n{proc.stderr[-1500:]}")
    print("[pypi] published")


def publish_npm() -> None:
    proc = run(["npm", "publish", "--access", "public"], cwd=PLUGIN_DIR)
    if proc.returncode != 0:
        raise SystemExit(f"npm publish failed:\n{proc.stderr[-1500:]}")
    print("[npm] published")


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--yes", action="store_true", help="真正发布（默认只校验）")
    parser.add_argument("--skip-pypi", action="store_true")
    parser.add_argument("--skip-npm", action="store_true")
    args = parser.parse_args()

    py_ver, npm_ver = versions()
    if py_ver != npm_ver:
        raise SystemExit(f"版本不一致：pyproject={py_ver} / package.json={npm_ver}")
    print(f"[check] version {py_ver} (python + npm 一致)")

    build()
    npm_pack_check()

    py_ok = pypi_ready()
    npm_ok = npm_ready()
    print(f"[check] PyPI token: {'yes' if py_ok else 'MISSING (UV_PUBLISH_TOKEN)'}")
    print(f"[check] npm login:  {'yes' if npm_ok else 'MISSING (npm login)'}")

    if not args.yes:
        print("\n[dry-run] 校验完成，未发布。加 --yes 且准备好上面两项凭证后执行。")
        print("[market] awesome-dsh-plugin 条目（发布成功后可提 PR）：\n" + MARKET_ENTRY)
        return 0

    if not args.skip_pypi:
        if not py_ok:
            raise SystemExit("缺少 PyPI 凭证：export UV_PUBLISH_TOKEN=...（或 uv publish --token）")
        publish_pypi()
    if not args.skip_npm:
        if not npm_ok:
            raise SystemExit("npm 未登录：先执行 npm login（或 npm config set //registry.npmjs.org/:_authToken=...）")
        publish_npm()

    print("\n[market] 下一步：把下面一行加到 awesome-dsh-plugin/README.md 的 "
          "'Tools & Capabilities' 分类，然后提 PR：\n" + MARKET_ENTRY)
    return 0


if __name__ == "__main__":
    sys.exit(main())
