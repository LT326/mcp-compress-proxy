"""真实 GitHub MCP server E2E：官方 github-mcp-server（Go，stdio）v2.0.3。

链路：
  A. 直连     客户端 ──stdio──> github-mcp-server
  B. 经代理   客户端 ──stdio──> mcp-compress-proxy ──stdio──> github-mcp-server

覆盖 profiles/github.yaml：
  list_issues（dict + 长 body）→ fallback 前 3 条 + body 首行
  list_pull_requests / list_commits（顶层数组）→ rows 信封
  search_repositories（dict + items[]）→ 前 3 条元数据
  get_me（compress_only_tools 之外）→ 原样透传
  retrieve_original → 原始文本逐字符还原

前置：GitHub token（GITHUB_PERSONAL_ACCESS_TOKEN，或本机 gh auth token）；
server 二进制默认 .e2e/tools/github/github-mcp-server.exe（缺失时用 gh 下载）。
用法：.venv\\Scripts\\python.exe scripts/e2e_mcp_github.py [--keep]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOGS_DIR = PROJECT_ROOT / ".e2e" / "logs"
TOOLS_DIR = PROJECT_ROOT / ".e2e" / "tools" / "github"
VERSION = os.environ.get("MCP_GITHUB_SERVER_VERSION", "v2.0.3")
PYTHON = sys.executable

ISSUE_REPO = ("cli", "cli")
SEARCH_QUERY = "mcp server language:python"


def find_exe() -> str | None:
    env = os.environ.get("MCP_GITHUB_SERVER_EXE")
    if env and Path(env).exists():
        return env
    exe = TOOLS_DIR / "github-mcp-server.exe"
    if exe.exists():
        return str(exe)
    gh = shutil.which("gh")
    if not gh:
        return None
    TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[env] downloading github-mcp-server {VERSION} ...")
    proc = subprocess.run(
        [
            gh, "release", "download", VERSION,
            "-R", "github/github-mcp-server",
            "-p", "github-mcp-server_Windows_x86_64.zip",
            "-D", str(TOOLS_DIR), "--clobber",
        ],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        print(f"[env] download failed: {proc.stderr[-300:]}")
        return None
    subprocess.run(
        [
            "powershell", "-NoProfile", "-Command",
            f"Expand-Archive -Path '{TOOLS_DIR / 'github-mcp-server_Windows_x86_64.zip'}' "
            f"-DestinationPath '{TOOLS_DIR}' -Force",
        ],
        check=False,
    )
    return str(exe) if exe.exists() else None


def find_token() -> str | None:
    token = os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        return token.strip()
    gh = shutil.which("gh")
    if not gh:
        return None
    proc = subprocess.run([gh, "auth", "token"], capture_output=True, text=True)
    if proc.returncode == 0 and proc.stdout.strip():
        return proc.stdout.strip()
    return None


def check(condition: bool, label: str, evidence: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[assert] {status} {label}" + (f" | {evidence[:160]}" if evidence else ""))
    if not condition:
        raise AssertionError(label)


class McpClient:
    def __init__(self, cmd: list[str], env: dict[str, str], log_path: Path, label: str):
        self.label = label
        self.log_path = log_path
        self._id = 0
        self._lock = threading.Lock()
        self._messages: list[dict] = []
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._err = open(log_path, "wb")
        self.proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._err, env=env
        )
        threading.Thread(target=self._read_stdout, daemon=True).start()
        print(f"[{label}] started pid={self.proc.pid}")

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

    def _wait(self, predicate, timeout: float, what: str) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                for msg in self._messages:
                    if predicate(msg):
                        return msg
            if self.proc.poll() is not None:
                raise RuntimeError(
                    f"[{self.label}] exited rc={self.proc.poll()} waiting {what}; "
                    f"stderr: {self.log_path}"
                )
            time.sleep(0.05)
        raise TimeoutError(f"[{self.label}] timeout waiting {what}; stderr: {self.log_path}")

    def request(self, method: str, params: dict | None = None, timeout: float = 120) -> dict:
        self._id += 1
        msg = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            msg["params"] = params
        self.proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
        self.proc.stdin.flush()
        return self._wait(
            lambda m: m.get("id") == self._id and ("result" in m or "error" in m),
            timeout, f"{method} response",
        )

    def initialize(self, timeout: float = 180) -> None:
        resp = self.request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "sw-e2e-github", "version": "0.1"},
            },
            timeout=timeout,
        )
        if "error" in resp:
            raise RuntimeError(f"initialize error: {resp['error']}")
        self.proc.stdin.write(
            (json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n").encode()
        )
        self.proc.stdin.flush()

    def call(self, name: str, arguments: dict, timeout: float = 180) -> str:
        resp = self.request("tools/call", {"name": name, "arguments": arguments}, timeout=timeout)
        if "error" in resp:
            raise RuntimeError(f"{name} JSON-RPC error: {resp['error']}")
        result = resp["result"]
        if result.get("isError"):
            raise RuntimeError(f"{name} tool error: {result}")
        return "\n".join(
            c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"
        )

    def close(self) -> None:
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=15)
        except Exception:
            self.proc.kill()
        self._err.close()
        print(f"[{self.label}] closed rc={self.proc.returncode}")


def proxy_command(db: Path, target: list[str]) -> list[str]:
    return [
        PYTHON, "-m", "mcp_compress_proxy.proxy",
        "--server", "github", "--db", str(db), "--", *target,
    ]


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()

    exe = find_exe()
    token = find_token()
    if not exe or not token:
        print(
            f"SKIP: github E2E needs the server binary ({exe or 'missing'}) and a token "
            f"({'set' if token else 'missing'}). Install with: gh release download "
            f"{VERSION} -R github/github-mcp-server "
            "-p github-mcp-server_Windows_x86_64.zip -D .e2e/tools/github"
        )
        return 0

    env = {**os.environ, "GITHUB_PERSONAL_ACCESS_TOKEN": token}
    target = [exe, "stdio"]
    base = Path(tempfile.mkdtemp(prefix="sw-e2e-github-"))
    print(f"[env] server={exe}; workdir={base}")

    direct = McpClient(target, env, LOGS_DIR / "github-direct.stderr.log", "gh-direct")
    try:
        direct.initialize()
        tools = direct.request("tools/list")["result"]["tools"]
        names = {t["name"] for t in tools}
        check({"list_issues", "list_pull_requests", "list_commits", "get_me"} <= names,
              "github tools present", str(len(names)) + " tools")

        direct_issues = direct.call("list_issues", {"owner": ISSUE_REPO[0], "repo": ISSUE_REPO[1], "perPage": 20})
        direct_prs = direct.call("list_pull_requests", {"owner": ISSUE_REPO[0], "repo": ISSUE_REPO[1], "perPage": 10})
        direct_commits = direct.call("list_commits", {"owner": ISSUE_REPO[0], "repo": ISSUE_REPO[1], "perPage": 20})
        direct_repos = direct.call("search_repositories", {"query": SEARCH_QUERY, "perPage": 10})
        direct_me = direct.call("get_me", {})
        direct_diff = direct.call(
            "pull_request_read",
            {"method": "get_diff", "owner": ISSUE_REPO[0], "repo": ISSUE_REPO[1], "pullNumber": 14846},
        )
        direct_pr = direct.call(
            "pull_request_read",
            {"method": "get", "owner": ISSUE_REPO[0], "repo": ISSUE_REPO[1], "pullNumber": 14846},
        )
        for name, text in (
            ("issues", direct_issues), ("prs", direct_prs), ("commits", direct_commits),
            ("repos", direct_repos),
        ):
            print(f"[direct] {name}: {len(text)} chars")
    finally:
        direct.close()

    db = base / "github-refs.db"
    proxy = McpClient(proxy_command(db, target), env, LOGS_DIR / "github-proxy.stderr.log", "gh-proxy")
    try:
        proxy.initialize()
        tools = proxy.request("tools/list")["result"]["tools"]
        check("retrieve_original" in {t["name"] for t in tools}, "retrieve_original injected")

        issues_text = proxy.call("list_issues", {"owner": ISSUE_REPO[0], "repo": ISSUE_REPO[1], "perPage": 20})
        issues = json.loads(issues_text)
        ref = issues.get("sw_ref", "")
        check(ref.startswith("sw-ref:"), "issues carry sw_ref", ref)
        check(len(issues["issues"]) == 3, "fallback keeps 3 issues", str(len(issues["issues"])))
        first_body = issues["issues"][0].get("body", "")
        check(
            "body" not in issues["issues"][0] or len(first_body.splitlines()) <= 2,
            "issue body reduced to at most 2 lines",
            (first_body.splitlines()[0] if first_body else "<no body>")[:60],
        )
        print(f"[proxy] issues {len(direct_issues)} -> {len(issues_text)} chars")

        restored = proxy.call("retrieve_original", {"ref_id": ref})
        check(restored == direct_issues, "retrieve restores full issues text")

        prs_text = proxy.call("list_pull_requests", {"owner": ISSUE_REPO[0], "repo": ISSUE_REPO[1], "perPage": 10})
        prs = json.loads(prs_text)
        check("rows" in prs and prs.get("sw_ref", "").startswith("sw-ref:"), "PR array enveloped")
        check(len(prs["rows"]) == 3, "fallback keeps 3 PRs", str(len(prs.get("rows", []))))
        pr_body = prs["rows"][0].get("body", "")
        check(
            "body" not in prs["rows"][0] or len(pr_body.splitlines()) <= 2,
            "PR body reduced to at most 2 lines",
            (pr_body.splitlines()[0] if pr_body else "<no body>")[:60],
        )
        print(f"[proxy] PRs {len(direct_prs)} -> {len(prs_text)} chars")

        commits_text = proxy.call("list_commits", {"owner": ISSUE_REPO[0], "repo": ISSUE_REPO[1], "perPage": 20})
        commits = json.loads(commits_text)
        check("rows" in commits and len(commits["rows"]) == 3, "commit array bounded to 3 rows")
        print(f"[proxy] commits {len(direct_commits)} -> {len(commits_text)} chars")

        repos_text = proxy.call("search_repositories", {"query": SEARCH_QUERY, "perPage": 10})
        repos = json.loads(repos_text)
        check(repos.get("sw_ref", "").startswith("sw-ref:"), "repo search carries sw_ref")
        check(len(repos["items"]) == 3, "repo items bounded to 3", str(len(repos.get("items", []))))
        print(f"[proxy] repos {len(direct_repos)} -> {len(repos_text)} chars")

        me_text = proxy.call("get_me", {})
        check(me_text == direct_me, "get_me passthrough (compress_only_tools)")
        check("sw-ref:" not in me_text, "get_me not enveloped")

        diff_text = proxy.call(
            "pull_request_read",
            {"method": "get_diff", "owner": ISSUE_REPO[0], "repo": ISSUE_REPO[1], "pullNumber": 14846},
        )
        check("sw-compressed" in diff_text and "sw-ref:" in diff_text, "PR diff compressed")
        check(len(diff_text) < len(direct_diff), "PR diff smaller", f"{len(direct_diff)} -> {len(diff_text)}")
        diff_ref = re.search(r"sw-ref:[0-9a-f]+", diff_text).group(0)
        restored_diff = proxy.call("retrieve_original", {"ref_id": diff_ref})
        check(restored_diff == direct_diff, "retrieve restores full PR diff")
        print(f"[proxy] PR diff {len(direct_diff)} -> {len(diff_text)} chars")

        pr_text = proxy.call(
            "pull_request_read",
            {"method": "get", "owner": ISSUE_REPO[0], "repo": ISSUE_REPO[1], "pullNumber": 14846},
        )
        check(pr_text == direct_pr, "PR JSON passthrough (text_only_tools)")
        check("sw-ref:" not in pr_text, "PR JSON not enveloped")
    finally:
        proxy.close()
        if not args.keep:
            shutil.rmtree(base, ignore_errors=True)

    print("\nALL PASS: github (direct + proxy + retrieve)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"\nFAILED: {exc}", file=sys.stderr)
        print(f"[diag] logs: {LOGS_DIR}", file=sys.stderr)
        sys.exit(1)