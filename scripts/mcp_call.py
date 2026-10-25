"""真实会话基准的双臂工具 helper：subagent 通过 Bash 调用，两臂工具面完全对称。

用法：
  python mcp_call.py <direct|compressed> <tool> '<json-args>'
  python mcp_call.py <direct|compressed> retrieve_original '{"ref_id": "sw-ref:..."}'

- direct     ：直连 mcp-server-git（无代理，输出原样）
- compressed ：经 mcp-compress-proxy（输出被压缩，retrieve_original 可用）
- repo_path 自动注入（args 里没有且工具需要时），LLM 无需关心仓库路径
- 每次调用把「实际打印给 LLM 的文本」记录到 metrics jsonl（两臂同格式）
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from zcode_entry import _patch_env  # noqa: E402  复用注册表 PATH / GIT_PYTHON_GIT_EXECUTABLE 修复

_patch_env()
SERVER_EXE = shutil.which("mcp-server-git") or "mcp-server-git"
VENV_PY = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
BENCH_DIR = PROJECT_ROOT / ".e2e" / "real-bench"
DEFAULT_REPO = BENCH_DIR / "real-repo"

ARG = sys.argv[1] if len(sys.argv) > 1 else ""
TOOL = sys.argv[2] if len(sys.argv) > 2 else ""
ARGS_JSON = sys.argv[3] if len(sys.argv) > 3 else "{}"


def build_command(repo: Path) -> list[str]:
    if ARG == "direct":
        return [SERVER_EXE, "--repository", str(repo)]
    if ARG == "compressed":
        return [
            str(VENV_PY), "-m", "mcp_compress_proxy.proxy",
            "--server", "git",
            "--db", str(BENCH_DIR / "refs.db"),
            "--", SERVER_EXE, "--repository", str(repo),
        ]
    raise SystemExit(f"arm must be direct|compressed, got {ARG!r}")


def main() -> int:
    _patch_env()
    BENCH_DIR.mkdir(parents=True, exist_ok=True)
    repo = DEFAULT_REPO

    args = json.loads(ARGS_JSON or "{}")
    if TOOL != "retrieve_original" and "repo_path" not in args:
        args["repo_path"] = str(repo)

    cmd = build_command(repo)
    env = {**os.environ}

    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, env=env)
    lock = threading.Lock()
    messages: list[dict] = []

    def reader():
        for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                with lock:
                    messages.append(json.loads(line))
            except json.JSONDecodeError:
                pass

    threading.Thread(target=reader, daemon=True).start()

    def request(method: str, params: dict | None, timeout: float) -> dict:
        req = {"jsonrpc": "2.0", "id": int(time.time() * 1000) % 10**9, "method": method}
        if params is not None:
            req["params"] = params
        proc.stdin.write((json.dumps(req) + "\n").encode("utf-8"))
        proc.stdin.flush()
        deadline = time.time() + timeout
        while time.time() < deadline:
            with lock:
                for m in messages:
                    if m.get("id") == req["id"] and ("result" in m or "error" in m):
                        return m
            if proc.poll() is not None:
                raise SystemExit(f"server exited rc={proc.returncode} during {method}")
            time.sleep(0.03)
        raise SystemExit(f"timeout waiting {method}")

    try:
        init = request("initialize", {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "real-bench", "version": "0"},
        }, timeout=120)
        if "error" in init:
            raise SystemExit(f"initialize error: {init['error']}")
        proc.stdin.write((json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n").encode())
        proc.stdin.flush()

        resp = request("tools/call", {"name": TOOL, "arguments": args}, timeout=120)
        if "error" in resp:
            text = f"JSON-RPC ERROR: {json.dumps(resp['error'], ensure_ascii=False)}"
        else:
            result = resp["result"]
            if result.get("isError"):
                text = "TOOL ERROR: " + "\n".join(
                    c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"
                )
            else:
                text = "\n".join(
                    c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"
                )
    finally:
        try:
            proc.stdin.close()
            proc.wait(timeout=10)
        except Exception:
            proc.kill()

    print(text)
    metric = {
        "ts": time.time(),
        "arm": ARG,
        "tool": TOOL,
        "args": {k: v for k, v in args.items() if k != "repo_path"},
        "sent_chars": len(text),
        "compressed": "sw-ref:" in text,
    }
    with open(BENCH_DIR / f"metrics-{ARG}.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(metric, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
