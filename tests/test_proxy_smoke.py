"""阶段三集成测试：拉起真实代理子进程，走 initialize → tools/list → tools/call → retrieve_original。"""

import asyncio
import json
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
