"""真实 MCP server 端到端验证：mcp-server-git（官方 PyPI 包，uvx 拉起）。

链路：
  A. 直连     客户端 ──stdio──> mcp-server-git
  B. 经代理   客户端 ──stdio──> mcp-compress-proxy ──stdio──> mcp-server-git

场景：
  A: initialize / tools/list / git_status / git_log / git_diff_unstaged（3 只读）
     + git_commit（1 写，验证仓库状态真实变化）
  B: tools/list（断言 retrieve_original 注入）/ git_commit（经代理写）
     / git_log（断言压缩：sw-ref 注入 + 中段省略）/ retrieve_original（断言完整还原）

用法：
  .venv\\Scripts\\python.exe scripts\\e2e_mcp_git.py [--keep] [--version X.Y.Z]

环境：uvx(uv) + git；PyPI 索引默认清华镜像（UV_INDEX_URL 可覆盖）。
退出码：0 全部通过；1 有失败（失败时保留临时目录与日志供诊断）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import winreg
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VERSION = os.environ.get("MCP_GIT_SERVER_VERSION", "2026.10.19")
MIRROR = os.environ.get("UV_INDEX_URL_OVERRIDE", "https://pypi.tuna.tsinghua.edu.cn/simple")
LOGS_DIR = PROJECT_ROOT / ".e2e" / "logs"
UV_CACHE = PROJECT_ROOT / ".e2e" / "uv-cache"

C1_MSG = "c1: initial readme"
C2_MSG = "c2: add app"
DIRECT_COMMIT_MSG = "e2e: commit staged notes (direct)"
PROXY_COMMIT_MSG = "e2e via proxy: compress roundtrip"
FIXTURE_COMMITS = 60


# ---------------------------------------------------------------- 工具链发现

def registry_path_entries() -> list[str]:
    """用户/系统注册表 PATH（agent 进程可能没继承完整 PATH）。"""
    entries: list[str] = []
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


def find_tool(name: str) -> str | None:
    """在进程 PATH + 注册表 PATH 中定位可执行文件（含 .exe/.cmd 垫片）。"""
    candidates = [name, f"{name}.exe", f"{name}.cmd", f"{name}.CMD", f"{name}.EXE"]
    dirs = os.environ.get("PATH", "").split(os.pathsep) + registry_path_entries()
    seen: set[str] = set()
    for d in dirs:
        if not d or d.lower() in seen:
            continue
        seen.add(d.lower())
        for cand in candidates:
            p = os.path.join(d, cand)
            if os.path.isfile(p):
                return p
    return shutil.which(name)


def toolchain_env() -> dict[str, str]:
    git = find_tool("git")
    uvx = find_tool("uvx")
    missing = [n for n, v in (("git", git), ("uvx", uvx)) if not v]
    if missing:
        raise SystemExit(f"FATAL: tool not found: {missing}")
    env = {
        **os.environ,
        "PATH": os.pathsep.join([os.path.dirname(git), os.path.dirname(uvx), os.environ.get("PATH", "")]),
        "UV_INDEX_URL": MIRROR,
        "UV_CACHE_DIR": str(UV_CACHE),
    }
    print(f"[env] node-not-required; git={git}; uvx={uvx}")
    out = subprocess.run([git, "--version"], capture_output=True, text=True)
    print(f"[env] {out.stdout.strip()}")
    print(f"[env] uv index: {MIRROR}")
    return env


def server_command(repo: Path, version: str) -> list[str]:
    uvx = find_tool("uvx")
    return [
        uvx,
        "--python", sys.executable,
        "--from", f"mcp-server-git=={version}",
        "mcp-server-git",
        "--repository", str(repo),
    ]


# ---------------------------------------------------------------- 临时仓库

def run_git(repo: Path, *args: str) -> str:
    out = subprocess.run(
        [find_tool("git"), "-C", str(repo), "-c", "core.autocrlf=false",
         "-c", "user.email=e2e@local", "-c", "user.name=SW E2E", *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if out.returncode != 0:
        raise RuntimeError(f"git {args} failed: {out.stderr.strip()}")
    return out.stdout


def make_repo(base: Path) -> Path:
    repo = base / "repo"
    repo.mkdir(parents=True)
    run_git(repo, "init", "-b", "main")
    (repo / "README.md").write_text("hello e2e\n", encoding="utf-8", newline="\n")
    run_git(repo, "add", "README.md")
    run_git(repo, "commit", "-m", C1_MSG)
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text("line1\nline2\n", encoding="utf-8", newline="\n")
    run_git(repo, "add", "src/app.py")
    run_git(repo, "commit", "-m", C2_MSG)
    (repo / "data").mkdir()
    for i in range(FIXTURE_COMMITS):
        (repo / "data" / "counter.txt").write_text(f"{i}\n", encoding="utf-8", newline="\n")
        run_git(repo, "add", "data/counter.txt")
        run_git(repo, "commit", "-m", f"fixture: change {i}")
    run_git(repo, "branch", "feature/demo")
    # 未暂存修改 + 已暂存新文件
    (repo / "src" / "app.py").write_text(
        "line1\nline2\nextra e2e line\n", encoding="utf-8", newline="\n"
    )
    (repo / "src" / "notes.txt").write_text("staged content\n", encoding="utf-8", newline="\n")
    run_git(repo, "add", "src/notes.txt")
    commits = run_git(repo, "rev-list", "--count", "HEAD").strip()
    print(f"[repo] {repo} commits={commits} (c1,c2 + {FIXTURE_COMMITS} fixture) + unstaged + staged")
    return repo


# ---------------------------------------------------------------- MCP 客户端

class McpClient:
    """行分隔 JSON-RPC over stdio 的最小同步客户端。"""

    def __init__(self, cmd: list[str], env: dict[str, str], stderr_path: Path, label: str):
        self.label = label
        self.stderr_path = stderr_path
        self._id = 0
        self._lock = threading.Lock()
        self._messages: list[dict] = []
        self._alive = True
        self._err = open(stderr_path, "wb")
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=self._err, env=env)
        self._reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._reader.start()
        print(f"[{label}] started pid={self.proc.pid} cmd[0]={Path(cmd[0]).name}")

    def _read_stdout(self) -> None:
        for raw in self.proc.stdout:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            with self._lock:
                self._messages.append(msg)
        self._alive = False

    def _wait(self, predicate, timeout: float, what: str) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                for msg in self._messages:
                    if predicate(msg):
                        return msg
            if self.proc.poll() is not None:
                raise RuntimeError(
                    f"[{self.label}] process exited rc={self.proc.poll()} while waiting for {what}; "
                    f"stderr log: {self.stderr_path}"
                )
            time.sleep(0.05)
        raise TimeoutError(f"[{self.label}] timeout waiting for {what} ({timeout}s); stderr log: {self.stderr_path}")

    def request(self, method: str, params: dict | None = None, timeout: float = 90) -> dict:
        self._id += 1
        msg = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            msg["params"] = params
        self.proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
        self.proc.stdin.flush()
        return self._wait(lambda m: m.get("id") == self._id and ("result" in m or "error" in m),
                          timeout, f"{method} response")

    def notify(self, method: str) -> None:
        msg = {"jsonrpc": "2.0", "method": method}
        self.proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def initialize(self, timeout: float = 240) -> dict:
        resp = self.request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "sw-e2e", "version": "0.1"},
        }, timeout=timeout)
        if "error" in resp:
            raise RuntimeError(f"initialize error: {resp['error']}")
        self.notify("notifications/initialized")
        return resp["result"]

    def tools_list(self) -> list[dict]:
        resp = self.request("tools/list")
        if "error" in resp:
            raise RuntimeError(f"tools/list error: {resp['error']}")
        return resp["result"]["tools"]

    def call(self, name: str, arguments: dict, timeout: float = 120) -> str:
        resp = self.request("tools/call", {"name": name, "arguments": arguments}, timeout=timeout)
        if "error" in resp:
            raise RuntimeError(f"{name} JSON-RPC error: {resp['error']}")
        result = resp["result"]
        if result.get("isError"):
            raise RuntimeError(f"{name} tool error: {result}")
        texts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
        return "\n".join(texts)

    def close(self) -> None:
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=15)
        except Exception:
            self.proc.kill()
        self._err.close()
        print(f"[{self.label}] closed rc={self.proc.returncode}")


def check(condition: bool, label: str, evidence: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[assert] {status} {label}" + (f" | {evidence[:160]}" if evidence else ""))
    if not condition:
        raise AssertionError(label)


# ---------------------------------------------------------------- 场景

def direct_scenario(repo: Path, env: dict[str, str], version: str) -> None:
    print("\n===== A. 直连 mcp-server-git =====")
    client = McpClient(server_command(repo, version), env, LOGS_DIR / "direct-server.stderr.log", "direct")
    try:
        t0 = time.time()
        init = client.initialize()
        print(f"[direct] initialize ok in {time.time()-t0:.1f}s: "
              f"serverInfo={init.get('serverInfo')} protocol={init.get('protocolVersion')}")
        check("git" in json.dumps(init.get("serverInfo", {})).lower(), "serverInfo contains 'git'")

        tools = client.tools_list()
        names = sorted(t["name"] for t in tools)
        print(f"[direct] tools/list ({len(names)}): {', '.join(names)}")
        check({"git_status", "git_log", "git_add", "git_commit"} <= set(names),
              "core tools present (git_status/git_log/git_add/git_commit)")

        # 只读 1：git_status —— 应含已暂存新文件与未暂存修改
        status = client.call("git_status", {"repo_path": str(repo)})
        check("src/notes.txt" in status, "git_status shows staged notes.txt", status)
        check("src/app.py" in status, "git_status shows modified app.py", status)

        # 只读 2：git_log（max_count 默认仅 10，显式放开）—— 首尾 commit 都可见
        log = client.call("git_log", {"repo_path": str(repo), "max_count": 1000})
        check(C1_MSG in log, "git_log contains first commit", log[:200])
        check(f"fixture: change {FIXTURE_COMMITS - 1}" in log, "git_log contains last fixture commit")
        print(f"[direct] git_log length: {len(log)} chars")

        # 只读 3：git_diff_unstaged —— 应含未暂存的追加行
        diff = client.call("git_diff_unstaged", {"repo_path": str(repo)})
        check("+extra e2e line" in diff, "git_diff_unstaged contains '+extra e2e line'", diff[:200])

        # 写工具：git_commit（提交已暂存的 notes.txt）
        out = client.call("git_commit", {"repo_path": str(repo), "message": DIRECT_COMMIT_MSG})
        print(f"[direct] git_commit ok: {out[:120]}")
        log2 = client.call("git_log", {"repo_path": str(repo), "max_count": 1000})
        check(DIRECT_COMMIT_MSG in log2, "git_log shows new commit after git_commit")
        status2 = client.call("git_status", {"repo_path": str(repo)})
        check("src/notes.txt" not in status2, "notes.txt no longer staged after commit", status2)
    finally:
        client.close()


def proxy_scenario(repo: Path, env: dict[str, str], version: str, tmp: Path) -> None:
    print("\n===== B. 经 mcp-compress-proxy =====")
    venv_py = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
    proxy_py = str(venv_py) if venv_py.exists() else sys.executable
    db_path = tmp / "proxy_refs.db"
    proxy_cmd = [
        proxy_py, "-m", "mcp_compress_proxy.proxy",
        "--server", "git", "--db", str(db_path),
        "--", *server_command(repo, version),
    ]
    client = McpClient(proxy_cmd, env, LOGS_DIR / "proxy.stderr.log", "proxy")
    try:
        init = client.initialize()
        print(f"[proxy] initialize ok: serverInfo={init.get('serverInfo')}")

        tools = client.tools_list()
        names = set(t["name"] for t in tools)
        print(f"[proxy] tools/list ({len(names)}): {', '.join(sorted(names))}")
        check("retrieve_original" in names, "retrieve_original injected into tools/list")
        check({"git_status", "git_log", "git_commit"} <= names, "original git tools still present")

        # 写工具经代理：git_add（把未暂存修改入暂存区）+ git_commit
        add_out = client.call("git_add", {"repo_path": str(repo), "files": ["src/app.py"]})
        print(f"[proxy] git_add ok: {add_out[:120]}")
        out = client.call("git_commit", {"repo_path": str(repo), "message": PROXY_COMMIT_MSG})
        print(f"[proxy] git_commit ok: {out[:120]}")

        # 短文本（git_status，提交后 working tree clean）应原样透传，不注入 sw-ref
        status = client.call("git_status", {"repo_path": str(repo)})
        check("Repository status" in status and "nothing to commit" in status
              and "sw-ref:" not in status,
              "short git_status passthrough unchanged (no sw-ref injection)", status)

        # 长文本（git_log，60+ commits）应被压缩：sw-ref 注入 + 中段省略
        log = client.call("git_log", {"repo_path": str(repo), "max_count": 1000})
        print(f"[proxy] git_log length after proxy: {len(log)} chars")
        check("sw-ref:" in log, "compressed git_log carries sw-ref marker", log[:300])
        check("lines omitted (sw-compressed)" in log, "omission marker present")
        check(PROXY_COMMIT_MSG in log, "newest commit visible in head window")
        mid_cut = f"fixture: change {FIXTURE_COMMITS // 2}"
        check(mid_cut not in log, f"mid-history '{mid_cut}' cut from middle window")

        # retrieve_original 还原完整原始输出
        ref = re.search(r"sw-ref:[0-9a-f]{12}", log)
        check(ref is not None, "sw-ref id extractable")
        restored = client.call("retrieve_original", {"ref_id": ref.group(0)})
        check(mid_cut in restored, f"retrieve_original restores cut line '{mid_cut}'")
        check(C1_MSG in restored and PROXY_COMMIT_MSG in restored, "restored log spans oldest..newest")
        print(f"[proxy] restored length: {len(restored)} chars vs compressed {len(log)}")

        check(db_path.exists() and db_path.stat().st_size > 0, "SQLite ref store created and non-empty")
    finally:
        client.close()


# ---------------------------------------------------------------- 主流程

def robust_rmtree(path: Path) -> None:
    def _chmod(func, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)
    shutil.rmtree(path, onerror=_chmod)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep", action="store_true", help="保留临时目录")
    parser.add_argument("--version", default=DEFAULT_VERSION, help="mcp-server-git 版本")
    args = parser.parse_args()

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    UV_CACHE.mkdir(parents=True, exist_ok=True)
    print(f"[env] python={sys.version.split()[0]} server=mcp-server-git=={args.version}")

    tmp = Path(tempfile.mkdtemp(prefix="sw-e2e-git-"))
    failure: Exception | None = None
    try:
        env = toolchain_env()
        repo = make_repo(tmp)
        direct_scenario(repo, env, args.version)
        proxy_scenario(repo, env, args.version, tmp)
        print("\n===== E2E ALL PASS =====")
    except Exception as exc:  # noqa: BLE001 —— 失败也要输出诊断再退出
        failure = exc
        print(f"\n===== E2E FAILED: {type(exc).__name__}: {exc} =====")
        print(f"[diag] temp dir kept: {tmp}")
        print(f"[diag] server stderr logs: {LOGS_DIR}")
        print(f"[diag] repro: git -C {tmp / 'repo'} log --oneline | head")
        print(f"[diag] repro: uvx --python {sys.executable} --from mcp-server-git=={args.version} "
              f"mcp-server-git --repository {tmp / 'repo'}")
    finally:
        if failure is None and not args.keep:
            robust_rmtree(tmp)
            print(f"[cleanup] removed {tmp}")
        elif failure is None:
            print(f"[cleanup] --keep: {tmp} retained")

    return 1 if failure else 0


if __name__ == "__main__":
    sys.exit(main())
