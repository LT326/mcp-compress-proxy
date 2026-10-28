"""真实 MCP server 端到端验证：filesystem（npx 2.0.1）、memory（npx 2.0.0）、
sqlite（uvx mcp-server-sqlite 0.1.0）。

链路：
  A. 直连     客户端 ──stdio──> 真实 server
  B. 经代理   客户端 ──stdio──> mcp-compress-proxy ──stdio──> 真实 server

覆盖的接入面（对应 profiles/filesystem.yaml v1.1 与 profiles/memory.yaml）：
  filesystem：list_directory 文本行 → 行数压缩；read_text_file 大文件 → 头尾压缩；
              get_file_info → skip 透传；structuredContent 副本同步压缩；retrieve 完整还原。
  memory：    create_entities → skip 透传；read_graph JSON → 保留实体/关系、
              observations 截断；retrieve 还原完整知识图谱。
  sqlite：    read_query 的 Python repr 数组 → JSON 信封 + 前 50 行；retrieve 还原
              原始 repr 文本，field_path 可直接取某行；list_tables 走 skip 透传。

用法：
  .venv\\Scripts\\python.exe scripts/e2e_mcp_extra.py [--keep]
环境：Node.js（npx）+ 网络（首次拉包）；退出码 0 全过，1 有失败。
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOGS_DIR = PROJECT_ROOT / ".e2e" / "logs"
FILESYSTEM_VERSION = os.environ.get("MCP_FS_SERVER_VERSION", "2.0.1")
MEMORY_VERSION = os.environ.get("MCP_MEMORY_SERVER_VERSION", "2.0.0")
PYTHON = sys.executable


def find_npx() -> str:
    for name in ("npx.cmd", "npx", "npx.exe"):
        found = shutil.which(name)
        if found:
            return found
    raise SystemExit("FATAL: npx not found; install Node.js >= 18")


def find_uvx() -> str:
    for name in ("uvx.exe", "uvx"):
        found = shutil.which(name)
        if found:
            return found
    raise SystemExit("FATAL: uvx not found; install uv (https://docs.astral.sh/uv/)")


def uv_env() -> dict[str, str]:
    return {
        **os.environ,
        "UV_INDEX_URL": os.environ.get(
            "UV_INDEX_URL_OVERRIDE", "https://pypi.tuna.tsinghua.edu.cn/simple"
        ),
        "UV_CACHE_DIR": str(PROJECT_ROOT / ".e2e" / "uv-cache"),
    }


def check(condition: bool, label: str, evidence: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[assert] {status} {label}" + (f" | {evidence[:160]}" if evidence else ""))
    if not condition:
        raise AssertionError(label)


class McpClient:
    """行分隔 JSON-RPC over stdio 的最小同步客户端。"""

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

    def _wait(self, predicate, timeout: float, what: str) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                for msg in self._messages:
                    if predicate(msg):
                        return msg
            if self.proc.poll() is not None:
                raise RuntimeError(
                    f"[{self.label}] exited rc={self.proc.poll()} while waiting for {what}; "
                    f"stderr: {self.log_path}"
                )
            time.sleep(0.05)
        raise TimeoutError(f"[{self.label}] timeout waiting for {what}; stderr: {self.log_path}")

    def request(self, method: str, params: dict | None = None, timeout: float = 120) -> dict:
        self._id += 1
        msg = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            msg["params"] = params
        self.proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
        self.proc.stdin.flush()
        return self._wait(
            lambda m: m.get("id") == self._id and ("result" in m or "error" in m),
            timeout,
            f"{method} response",
        )

    def initialize(self, timeout: float = 300) -> dict:
        resp = self.request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "sw-e2e-extra", "version": "0.1"},
            },
            timeout=timeout,
        )
        if "error" in resp:
            raise RuntimeError(f"initialize error: {resp['error']}")
        self.proc.stdin.write(
            (json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n").encode()
        )
        self.proc.stdin.flush()
        return resp["result"]

    def tools_list(self) -> list[dict]:
        resp = self.request("tools/list")
        if "error" in resp:
            raise RuntimeError(f"tools/list error: {resp['error']}")
        return resp["result"]["tools"]

    def call_result(self, name: str, arguments: dict, timeout: float = 180) -> dict:
        resp = self.request("tools/call", {"name": name, "arguments": arguments}, timeout=timeout)
        if "error" in resp:
            raise RuntimeError(f"{name} JSON-RPC error: {resp['error']}")
        result = resp["result"]
        if result.get("isError"):
            raise RuntimeError(f"{name} tool error: {result}")
        return result

    def call(self, name: str, arguments: dict, timeout: float = 180) -> str:
        result = self.call_result(name, arguments, timeout=timeout)
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


def proxy_command(server: str, db: Path, target: list[str]) -> list[str]:
    return [
        PYTHON,
        "-m",
        "mcp_compress_proxy.proxy",
        "--server",
        server,
        "--db",
        str(db),
        "--",
        *target,
    ]


def make_tree(base: Path) -> Path:
    tree = base / "tree"
    tree.mkdir(parents=True)
    for i in range(220):
        (tree / f"file_{i:03d}.txt").write_text(f"content {i}\n", encoding="utf-8")
    src = tree / "src"
    src.mkdir()
    lines = []
    for i in range(3000):
        if i == 500:
            lines.append('    raise RuntimeError("unexpected state in phase transition")')
        elif i % 50 == 0:
            lines.append(f"def handler_{i}(request, context):")
        else:
            lines.append(f"    value_{i % 50} = transform(payload_{i % 7}, offset={i})")
    (src / "big.py").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[tree] {tree} files={221}")
    return tree


def filesystem_e2e(tree: Path, base: Path, npx: str) -> None:
    print("\n===== filesystem: 直连 =====")
    target = [npx, "-y", f"@modelcontextprotocol/server-filesystem@{FILESYSTEM_VERSION}", str(tree)]
    env = dict(os.environ)
    direct = McpClient(target, env, LOGS_DIR / "fs-direct.stderr.log", "fs-direct")
    try:
        direct.initialize()
        names = {t["name"] for t in direct.tools_list()}
        check("list_directory" in names and "read_text_file" in names, "filesystem tools present")
        direct_listing = direct.call("list_directory", {"path": str(tree)})
        check("[FILE]" in direct_listing, "list_directory returns [FILE] lines", direct_listing[:80])
        check(len(direct_listing.splitlines()) >= 220, "listing covers 220 files")
        direct_big = direct.call("read_text_file", {"path": str(tree / "src" / "big.py")})
        check("unexpected state in phase transition" in direct_big, "read_text_file returns full content")
    finally:
        direct.close()

    print("\n===== filesystem: 经代理 =====")
    db = base / "fs-refs.db"
    proxy = McpClient(
        proxy_command("filesystem", db, target), env, LOGS_DIR / "fs-proxy.stderr.log", "fs-proxy"
    )
    try:
        proxy.initialize()
        names = {t["name"] for t in proxy.tools_list()}
        check("retrieve_original" in names, "retrieve_original injected")

        listing_result = proxy.call_result("list_directory", {"path": str(tree)})
        listing = "\n".join(
            c.get("text", "") for c in listing_result.get("content", []) if c.get("type") == "text"
        )
        check("sw-compressed" in listing and "sw-ref:" in listing, "listing compressed with ref")
        check(len(listing.splitlines()) < 220, "listing truncated", f"{len(listing.splitlines())} lines")
        structured = listing_result.get("structuredContent")
        check(
            isinstance(structured, dict) and str(structured.get("sw_ref", "")).startswith("sw-ref:"),
            "structuredContent compressed too (no full duplicate)",
        )
        ref = re.search(r"sw-ref:[0-9a-f]+", listing).group(0)
        restored = proxy.call("retrieve_original", {"ref_id": ref})
        check(restored == direct_listing, "retrieve restores full listing")

        big = proxy.call("read_text_file", {"path": str(tree / "src" / "big.py")})
        check("sw-compressed" in big, "big file compressed")
        check(len(big) < len(direct_big) / 4, "big file at least 4x smaller",
              f"{len(direct_big)} -> {len(big)}")
        big_ref = re.search(r"sw-ref:[0-9a-f]+", big).group(0)
        restored_big = proxy.call("retrieve_original", {"ref_id": big_ref})
        check(restored_big == direct_big, "retrieve restores full file content")
        print(
            f"[fs] listing {len(direct_listing)} -> {len(listing)} chars; "
            f"big file {len(direct_big)} -> {len(big)} chars"
        )

        info = proxy.call("get_file_info", {"path": str(tree / "src" / "big.py")})
        check("sw-compressed" not in info, "get_file_info passes through (skip rule)", info[:80])
    finally:
        proxy.close()


def memory_e2e(base: Path, npx: str) -> None:
    memory_file = base / "memory.json"
    env = {**os.environ, "MEMORY_FILE_PATH": str(memory_file)}
    target = [npx, "-y", f"@modelcontextprotocol/server-memory@{MEMORY_VERSION}"]

    entities = [
        {
            "name": f"service_{i}",
            "entityType": "service",
            "observations": [
                f"obs {j} of service_{i}: " + "long explanatory text " * 8 for j in range(6)
            ],
        }
        for i in range(6)
    ]
    relations = [
        {"from": f"service_{i}", "to": f"service_{(i + 1) % 6}", "relationType": "depends_on"}
        for i in range(5)
    ]

    print("\n===== memory: 直连（写入种子数据） =====")
    direct = McpClient(target, env, LOGS_DIR / "mem-direct.stderr.log", "mem-direct")
    try:
        direct.initialize()
        direct.call("create_entities", {"entities": entities})
        direct.call("create_relations", {"relations": relations})
        graph_text = direct.call("read_graph", {})
        graph = json.loads(graph_text)
        check(len(graph["entities"]) == 6, "direct read_graph has 6 entities")
        check(len(graph["entities"][0]["observations"]) == 6, "direct observations intact")
        check(len(graph["relations"]) == 5, "direct relations intact")
    finally:
        direct.close()

    print("\n===== memory: 经代理 =====")
    db = base / "mem-refs.db"
    proxy = McpClient(
        proxy_command("memory", db, target), env, LOGS_DIR / "mem-proxy.stderr.log", "mem-proxy"
    )
    try:
        proxy.initialize()
        names = {t["name"] for t in proxy.tools_list()}
        check("retrieve_original" in names, "retrieve_original injected")

        created = proxy.call("create_entities", {"entities": [{"name": "extra", "entityType": "x", "observations": []}]})
        check("sw-ref:" not in created, "create_entities passes through (skip rule)", created[:80])

        compressed_text = proxy.call("read_graph", {})
        compressed = json.loads(compressed_text)
        ref = compressed.get("sw_ref", "")
        check(ref.startswith("sw-ref:"), "read_graph carries sw_ref", ref)
        check(len(compressed["entities"]) <= 3, "fallback keeps <=3 entities", str(len(compressed["entities"])))
        check(len(compressed["entities"][0]["observations"]) <= 2, "observations truncated")
        obs_total = sum(len(e.get("observations", [])) for e in compressed["entities"])
        print(
            f"[mem] entities={[e['name'] for e in compressed['entities']]} "
            f"observations={[len(e.get('observations', [])) for e in compressed['entities']]}"
        )
        check(0 < obs_total <= 6, "only the first 2 observations per entity survive", str(obs_total))
        check(len(compressed["relations"]) <= 3, "relations bounded")

        print(f"[mem] graph {len(graph_text)} -> {len(compressed_text)} chars")
        restored_text = proxy.call("retrieve_original", {"ref_id": ref})
        restored = json.loads(restored_text)
        check(len(restored["entities"]) == 7, "retrieve restores all 7 entities")
        check(len(restored["entities"][0]["observations"]) == 6, "retrieve restores observations")
    finally:
        proxy.close()


def make_sqlite_db(base: Path) -> Path:
    db_file = base / "sample.db"
    conn = sqlite3.connect(db_file)
    try:
        conn.execute("create table items(id integer primary key, name text, payload text)")
        conn.executemany(
            "insert into items(name, payload) values(?, ?)",
            [(f"item{i}", "x" * 120) for i in range(120)],
        )
        conn.commit()
    finally:
        conn.close()
    print(f"[db] {db_file} rows=120")
    return db_file


def sqlite_e2e(base: Path, uvx: str) -> None:
    db_file = make_sqlite_db(base)
    env = uv_env()
    # mcp-server-sqlite 0.1.0 仍用 mcp<2 的旧 API（Server.list_resources），
    # 所以 uvx 里显式把 mcp 钉在 1.x。
    target = [
        uvx, "--with", "mcp<2", "mcp-server-sqlite", "--db-path", str(db_file),
    ]

    print("\n===== sqlite: 直连 =====")
    direct = McpClient(target, env, LOGS_DIR / "sqlite-direct.stderr.log", "sqlite-direct")
    try:
        direct.initialize()
        names = {t["name"] for t in direct.tools_list()}
        check("read_query" in names and "list_tables" in names, "sqlite tools present")
        direct_rows = direct.call("read_query", {"query": "select * from items"})
        check(direct_rows.startswith("[{'id'"), "read_query returns Python repr", direct_rows[:60])
        check("'id': 119" in direct_rows, "direct query has all 120 rows")
    finally:
        direct.close()

    print("\n===== sqlite: 经代理 =====")
    db = base / "sqlite-refs.db"
    proxy = McpClient(
        proxy_command("sqlite", db, target), env, LOGS_DIR / "sqlite-proxy.stderr.log", "sqlite-proxy"
    )
    try:
        proxy.initialize()
        names = {t["name"] for t in proxy.tools_list()}
        check("retrieve_original" in names, "retrieve_original injected")

        compressed_text = proxy.call("read_query", {"query": "select * from items"})
        compressed = json.loads(compressed_text)
        ref = compressed.get("sw_ref", "")
        check(ref.startswith("sw-ref:"), "array payload carries sw_ref", ref)
        check(len(compressed["rows"]) == 50, "fallback keeps first 50 rows", str(len(compressed["rows"])))
        check(
            compressed["rows"][0] == ast.literal_eval(direct_rows)[0],
            "first row intact",
            str(compressed["rows"][0])[:80],
        )
        print(f"[sqlite] query {len(direct_rows)} -> {len(compressed_text)} chars")

        restored = proxy.call("retrieve_original", {"ref_id": ref})
        check(restored == direct_rows, "retrieve restores exact Python repr text")

        partial = proxy.call("retrieve_original", {"ref_id": ref, "field_path": "57.name"})
        check(partial == "item57", "partial retrieve by row/field works", partial)

        tables = proxy.call("list_tables", {})
        check("items" in tables, "list_tables returns the table name", tables[:80])
        check("sw-ref:" not in tables, "list_tables passthrough (skip rule, not enveloped)")
    finally:
        proxy.close()


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--keep", action="store_true", help="keep temp dir")
    args = parser.parse_args()

    npx = find_npx()
    print(f"[env] npx={npx}; python={PYTHON}")
    base = Path(tempfile.mkdtemp(prefix="sw-e2e-extra-"))
    print(f"[env] workdir={base}")
    try:
        tree = make_tree(base)
        filesystem_e2e(tree, base, npx)
        memory_e2e(base, npx)
        sqlite_e2e(base, find_uvx())
        print("\nALL PASS: filesystem + memory + sqlite (direct + proxy + retrieve)")
        return 0
    except Exception as exc:
        print(f"\nFAILED: {exc}", file=sys.stderr)
        print(f"[diag] logs: {LOGS_DIR}; workdir kept: {base}", file=sys.stderr)
        return 1
    finally:
        if not args.keep and sys.exc_info()[0] is None:
            shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())