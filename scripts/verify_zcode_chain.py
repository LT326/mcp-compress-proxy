"""用"模拟 ZCode 的残缺环境"验证新链路：launcher + 代理 + 固化 server exe。

env 刻意只给 SystemRoot（无 PATH/TEMP/APPDATA），比 ZCode 实际环境更苛刻；
若此环境下握手与工具调用都正常，则 ZCode 里必然正常。
"""

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

PY = r"E:\GLM\mcp-compress-proxy\.venv\Scripts\python.exe"
LAUNCHER = r"E:\GLM\mcp-compress-proxy\scripts\zcode_entry.py"
SERVER_EXE = r"C:\Users\a\.local\bin\mcp-server-git.exe"
DB = r"E:\GLM\mcp-compress-proxy\.e2e\verify_refs.db"
REPO = r"E:\GLM\mcp-compress-proxy\.e2e\demo-repo"

CMD = [PY, LAUNCHER, "--server", "git", "--db", DB, "--", SERVER_EXE, "--repository", REPO]


class MiniClient:
    def __init__(self, cmd, env, label):
        self.label = label
        self._id = 0
        self._lock = threading.Lock()
        self._msgs = []
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, env=env)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for raw in self.proc.stdout:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                with self._lock:
                    self._msgs.append(json.loads(line))
            except json.JSONDecodeError:
                pass

    def request(self, method, params=None, timeout=90):
        self._id += 1
        msg = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            msg["params"] = params
        self.proc.stdin.write((json.dumps(msg) + "\n").encode())
        self.proc.stdin.flush()
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                for m in self._msgs:
                    if m.get("id") == self._id:
                        return m
            if self.proc.poll() is not None:
                raise RuntimeError(f"[{self.label}] exited rc={self.proc.returncode}")
            time.sleep(0.05)
        raise TimeoutError(f"[{self.label}] timeout waiting {method}")

    def notify(self, method):
        self.proc.stdin.write((json.dumps({"jsonrpc": "2.0", "method": method}) + "\n").encode())
        self.proc.stdin.flush()


def run_case(label, env):
    print(f"\n=== env case: {label} ===")
    t0 = time.time()
    c = MiniClient(CMD, env, label)
    try:
        init = c.request("initialize", {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "verify", "version": "0"},
        })
        dt = time.time() - t0
        print(f"  initialize ok in {dt:.1f}s: {init['result'].get('serverInfo')}")
        assert dt < 15, "handshake too slow"
        c.notify("notifications/initialized")
        tools = c.request("tools/list")["result"]["tools"]
        names = [t["name"] for t in tools]
        print(f"  tools: {len(names)} (retrieve_original={'retrieve_original' in names})")
        assert "retrieve_original" in names and "git_log" in names
        log = c.request("tools/call", {
            "name": "git_log",
            "arguments": {"repo_path": REPO, "max_count": 20},
        })
        text = log["result"]["content"][0]["text"]
        compressed = "sw-ref:" in text
        print(f"  git_log: {len(text)} chars, compressed={compressed}, "
              f"omitted_marker={'lines omitted (sw-compressed)' in text}")
        assert "demo: initial readme" in text or compressed, "git_log content unexpected"

        if compressed:
            import re
            ref = re.search(r"sw-ref:[0-9a-f]{12}", text).group(0)
            rt = c.request("tools/call", {
                "name": "retrieve_original", "arguments": {"ref_id": ref},
            })
            result = rt["result"]
            restored = result["content"][0]["text"]
            print(f"  retrieve_original: resultType={result.get('resultType')!r}, "
                  f"restored={len(restored)} chars, has_cut_content={'demo: add note 8' in restored}")
            # MCP 2026-07-28：必须带 resultType，且还原出被裁的中段内容
            assert result.get("resultType") == "complete"
            assert result.get("isError") is False
            assert "demo: add note 8" in restored
        return True
    finally:
        c.proc.stdin.close()
        try:
            c.proc.wait(timeout=10)
        except Exception:
            c.proc.kill()


if __name__ == "__main__":
    ok1 = run_case("ZCode-simulated (only SystemRoot)", {"SystemRoot": r"C:\Windows"})
    ok2 = run_case("even harsher (fully empty env)", {})
    print("\nVERIFY:", "ALL PASS" if (ok1 and ok2) else "FAILED")
    sys.exit(0 if (ok1 and ok2) else 1)
