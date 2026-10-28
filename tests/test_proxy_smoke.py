"""阶段三集成测试：拉起真实代理子进程，走 initialize → tools/list → tools/call → retrieve_original。"""

import asyncio
import json
import re
import sys
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


async def _drive(db_path: str) -> None:
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "mcp_compress_proxy.proxy",
        "--server",
        "git",
        "--db",
        db_path,
        "--",
        sys.executable,
        str(FIXTURES / "fake_server.py"),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
    )

    async def send(obj: dict) -> None:
        proc.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
        await proc.stdin.drain()

    async def read_until(pred, timeout: float = 15.0) -> dict:
        while True:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout)
            assert line, "proxy exited unexpectedly"
            msg = json.loads(line.decode("utf-8"))
            if pred(msg):
                return msg

    try:
        # initialize
        await send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0"},
                },
            }
        )
        init = await read_until(lambda m: m.get("id") == 1)
        assert "result" in init

        await send({"jsonrpc": "2.0", "method": "notifications/initialized"})

        # tools/list：返回中包含注入的 retrieve_original
        await send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        listing = await read_until(lambda m: m.get("id") == 2)
        names = [t["name"] for t in listing["result"]["tools"]]
        assert "git_log" in names
        assert "retrieve_original" in names

        # tools/call：响应被压缩，携带 sw-ref
        await send(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "git_log", "arguments": {"repo": "."}},
            }
        )
        call = await read_until(lambda m: m.get("id") == 3)
        payload = json.loads(call["result"]["content"][0]["text"])
        ref = payload["sw_ref"]
        assert ref.startswith("sw-ref:")
        assert len(payload["commits"]) < 8  # 占位 profile 下走通用回退（数组前 3 项）

        # structuredContent 副本也必须压缩，否则完整副本会让压缩收益归零
        structured = call["result"]["structuredContent"]
        assert structured["sw_ref"].startswith("sw-ref:")
        assert len(structured["commits"]) < 8

        # retrieve_original：代理本地处理，能还原完整原始 payload
        await send(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "retrieve_original", "arguments": {"ref_id": ref}},
            }
        )
        retrieved = await read_until(lambda m: m.get("id") == 4)
        # MCP 2026-07-28：代理本地生成的工具结果必须带 resultType
        assert retrieved["result"]["resultType"] == "complete"
        restored = json.loads(retrieved["result"]["content"][0]["text"])
        assert len(restored["commits"]) == 8  # 原始 8 条 commit 完整还原

        # 无效 field_path → JSON-RPC error，message 含 "path not found"
        await send(
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {
                    "name": "retrieve_original",
                    "arguments": {"ref_id": ref, "field_path": "commits.99.hash"},
                },
            }
        )
        err = await read_until(lambda m: m.get("id") == 5)
        assert "path not found" in err["error"]["message"]
    finally:
        proc.stdin.close()
        await asyncio.wait_for(proc.wait(), 10)


def test_proxy_smoke(tmp_path):
    asyncio.run(_drive(str(tmp_path / "smoke_refs.db")))

async def _drive_sqlite(db_path: str) -> None:
    """sqlite：Python repr 数组 → 压缩成 rows 信封 + 原文可逆取回。"""
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "mcp_compress_proxy.proxy",
        "--server",
        "sqlite",
        "--db",
        db_path,
        "--",
        sys.executable,
        str(FIXTURES / "fake_server.py"),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
    )

    async def send(obj: dict) -> None:
        proc.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
        await proc.stdin.drain()

    async def read_until(pred, timeout: float = 15.0) -> dict:
        while True:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout)
            assert line, "proxy exited unexpectedly"
            msg = json.loads(line.decode("utf-8"))
            if pred(msg):
                return msg

    try:
        await send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0"},
                },
            }
        )
        await read_until(lambda m: m.get("id") == 1)
        await send({"jsonrpc": "2.0", "method": "notifications/initialized"})

        await send(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "read_query", "arguments": {"query": "select * from items"}},
            }
        )
        call = await read_until(lambda m: m.get("id") == 2)
        payload = json.loads(call["result"]["content"][0]["text"])
        assert payload["sw_ref"].startswith("sw-ref:")
        assert len(payload["rows"]) == 50  # fallback rows[0:50]
        assert payload["rows"][0]["id"] == 0

        await send(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "retrieve_original", "arguments": {"ref_id": payload["sw_ref"]}},
            }
        )
        retrieved = await read_until(lambda m: m.get("id") == 3)
        assert retrieved["result"]["resultType"] == "complete"
        # 原文是 Python repr（单引号），必须逐字符还原
        original = retrieved["result"]["content"][0]["text"]
        assert original.startswith("[{'id': 0")
        assert "'id': 59" in original
    finally:
        proc.stdin.close()
        await asyncio.wait_for(proc.wait(), 10)


def test_proxy_sqlite_array_smoke(tmp_path):
    asyncio.run(_drive_sqlite(str(tmp_path / "sqlite_refs.db")))

async def _drive_github(db_path: str) -> None:
    """github：只压 compress_only_tools 内的工具，其余透传。"""
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "mcp_compress_proxy.proxy",
        "--server",
        "github",
        "--db",
        db_path,
        "--",
        sys.executable,
        str(FIXTURES / "fake_server.py"),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
    )

    async def send(obj: dict) -> None:
        proc.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
        await proc.stdin.drain()

    async def read_until(pred, timeout: float = 15.0) -> dict:
        while True:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout)
            assert line, "proxy exited unexpectedly"
            msg = json.loads(line.decode("utf-8"))
            if pred(msg):
                return msg

    try:
        await send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0"},
                },
            }
        )
        await read_until(lambda m: m.get("id") == 1)
        await send({"jsonrpc": "2.0", "method": "notifications/initialized"})

        await send(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "list_issues", "arguments": {"owner": "cli"}},
            }
        )
        call = await read_until(lambda m: m.get("id") == 2)
        original = call["result"]["content"][0]["text"]
        payload = json.loads(original)
        assert payload["sw_ref"].startswith("sw-ref:")
        assert len(payload["issues"]) == 3  # fallback 前 3 条
        assert len(payload["issues"][0]["body"].splitlines()) <= 2  # 首行 + 省略标记

        await send(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "retrieve_original", "arguments": {"ref_id": payload["sw_ref"]}},
            }
        )
        retrieved = await read_until(lambda m: m.get("id") == 3)
        assert retrieved["result"]["resultType"] == "complete"
        restored = json.loads(retrieved["result"]["content"][0]["text"])
        assert len(restored["issues"]) == 20
        assert "detail 39" in restored["issues"][0]["body"]

        # compress_only_tools 之外：get_me 原样透传
        await send(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "get_me", "arguments": {}},
            }
        )
        me = await read_until(lambda m: m.get("id") == 4)
        assert me["result"]["content"][0]["text"] == '{"login": "octocat"}'

        # pull_request_read：get_diff 是文本 → 压缩；get 是 JSON → 透传
        await send(
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {
                    "name": "pull_request_read",
                    "arguments": {"method": "get_diff", "owner": "cli", "repo": "cli", "pullNumber": 1},
                },
            }
        )
        diff = await read_until(lambda m: m.get("id") == 5)
        diff_text = diff["result"]["content"][0]["text"]
        assert "sw-compressed" in diff_text and "sw-ref:" in diff_text
        diff_ref = re.search(r"sw-ref:[0-9a-f]+", diff_text).group(0)

        await send(
            {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {
                    "name": "retrieve_original",
                    "arguments": {"ref_id": diff_ref},
                },
            }
        )
        restored_diff = await read_until(lambda m: m.get("id") == 6)
        full_diff = restored_diff["result"]["content"][0]["text"]
        assert "diff --git a/file_199" in full_diff
        assert len(full_diff) > len(diff_text) * 2

        await send(
            {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tools/call",
                "params": {
                    "name": "pull_request_read",
                    "arguments": {"method": "get", "owner": "cli", "repo": "cli", "pullNumber": 1},
                },
            }
        )
        pr_json = await read_until(lambda m: m.get("id") == 7)
        assert pr_json["result"]["content"][0]["text"] == '{"number": 7, "title": "a PR", "body": "body text"}'
    finally:
        proc.stdin.close()
        await asyncio.wait_for(proc.wait(), 10)


def test_proxy_github_compress_only_smoke(tmp_path):
    asyncio.run(_drive_github(str(tmp_path / "github_refs.db")))