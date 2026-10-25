"""stdio 透明代理主循环。

用法：
    python -m mcp_compress_proxy.proxy [--server NAME] [--db PATH] [--profiles-dir PATH] --
        <原始 MCP server command> [args...]

行为（AGENTS.md 约束 5 / R6 / R7）：
- 客户端 ←→ 代理走 stdio；代理 spawn 原始 server 子进程
- initialize / tools/list 的返回工具列表追加 retrieve_original
- tools/call(retrieve_original) 由代理本地查 SQLite 直接返回，不转发子进程
- 其他 tools/call 响应：按滚动窗口上下文检测任务类型 → 加载 Profile → 压缩，
  原始 payload 由引擎落 SQLite
- 子进程 stderr 直通本进程 stderr；stdout 为 JSON-RPC 通道逐行实时处理
- 压缩路径 fail-open：压缩异常时原样返回，透明性优先
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import threading
from collections import deque
from pathlib import Path
from typing import Any

from ..compressor.engine import compress, default_db_path
from ..profiles.loader import DEFAULT_PROFILES_DIR, load_profile
from ..store.sqlite_store import SqliteStore
from ..task_context.detector import TaskType, detect
from ..tools.retrieve import RETRIEVE_ORIGINAL_TOOL, handle_retrieve

CONTEXT_WINDOW = 5


def guess_server_name(server_cmd: list[str], profiles_dir: Path) -> str | None:
    """从子进程命令串推断 server 名：匹配 profiles/*.yaml 的 stem（最长优先）。"""
    joined = " ".join(server_cmd).lower()
    candidates = sorted((p.stem for p in profiles_dir.glob("*.yaml")), key=len, reverse=True)
    for name in candidates:
        if name.lower() in joined:
            return name
    return None


def extract_context_text(arguments: Any) -> str:
    """从 tools/call 参数提取自由文本（字符串值）拼接，作为任务上下文信号。

    边界（AGENTS.md R9）：代理看不到对话原文，只能拿到工具参数文本，
    信号弱于真实对话，unknown/fallback 触发率会偏高。
    """
    parts: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, str):
            parts.append(node)
        elif isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(arguments)
    return " ".join(parts)


class Proxy:
    def __init__(
        self,
        server_cmd: list[str],
        server_name: str | None = None,
        db_path: Path | str | None = None,
        profiles_dir: Path | str | None = None,
    ):
        self.server_cmd = server_cmd
        self.server_name = server_name
        self.db_path = Path(db_path) if db_path else default_db_path()
        self.profiles_dir = Path(profiles_dir) if profiles_dir else DEFAULT_PROFILES_DIR
        self.store: SqliteStore | None = None
        self.profile = None
        self.context: deque[str] = deque(maxlen=CONTEXT_WINDOW)
        self.pending: dict[Any, str] = {}  # 代理已转发请求 id -> method
        self.proc: asyncio.subprocess.Process | None = None

    async def run(self) -> None:
        # Windows 兼容：客户端给的命令可能是裸名（npx/uvx）或 .cmd 垫片，
        # spawn 前用 PATH 解析成可执行绝对路径（GUI 客户端的 PATH 可能很精简）
        resolved = shutil.which(self.server_cmd[0])
        if resolved:
            self.server_cmd = [resolved, *self.server_cmd[1:]]

        self.store = SqliteStore(self.db_path)
        name = self.server_name or guess_server_name(self.server_cmd, self.profiles_dir)
        if name:
            try:
                self.profile = load_profile(name, self.profiles_dir)
            except FileNotFoundError:
                print(f"[sw-proxy] no profile for server {name!r}; passthrough", file=sys.stderr)

        self.proc = await asyncio.create_subprocess_exec(
            *self.server_cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,  # stderr 继承本进程 stderr，实时直通
            # 单行 JSON-RPC 可能远超 asyncio 默认 64KB 行上限（如大 diff 的
            # git_show 响应），不调大LimitOverrunError 会直接打崩代理（实测踩中）
            limit=64 * 1024 * 1024,
        )
        await asyncio.gather(self._read_client(), self._read_server())

    # ---------- 客户端 → 子进程 ----------

    async def _read_client(self) -> None:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[str | None] = asyncio.Queue()

        def _pump() -> None:
            try:
                for line in sys.stdin:
                    loop.call_soon_threadsafe(queue.put_nowait, line)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        threading.Thread(target=_pump, daemon=True).start()

        while True:
            line = await queue.get()
            if line is None:
                break
            line = line.strip()
            if line:
                await self._handle_client_line(line)
        if self.proc and self.proc.stdin:
            self.proc.stdin.close()

    async def _handle_client_line(self, line: str) -> None:
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            self._emit_raw(line)  # 非 JSON 原样透传
            return

        method = msg.get("method")
        if method is None:  # 客户端对服务器发起请求的响应等，原样转发
            await self._write_child(line)
            return

        msg_id = msg.get("id")
        is_notification = msg_id is None
        params = msg.get("params") or {}

        if method == "tools/call" and params.get("name") == "retrieve_original":
            if not is_notification:  # R6：本地处理，绝不转发
                result, error = handle_retrieve(self.store, params.get("arguments"))
                self._respond(msg_id, result=result, error=error)
            return

        if method == "tools/call" and not is_notification:
            text = extract_context_text(params.get("arguments"))
            if text:
                self.context.append(text)
        if not is_notification:
            self.pending[msg_id] = {"method": method, "tool": params.get("name"), "args": params.get("arguments")}
        await self._write_child(line)

    # ---------- 子进程 → 客户端 ----------

    async def _read_server(self) -> None:
        assert self.proc and self.proc.stdout
        async for raw in self.proc.stdout:
            line = raw.decode("utf-8", errors="replace").strip()
            if line:
                await self._handle_server_line(line)

    async def _handle_server_line(self, line: str) -> None:
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            self._emit_raw(line)
            return

        msg_id = msg.get("id")
        if (
            msg_id is not None
            and ("result" in msg or "error" in msg)
            and msg_id in self.pending
        ):
            entry = self.pending.pop(msg_id)
            method = entry["method"]
            if isinstance(msg.get("result"), dict):
                if method == "tools/call":
                    msg["result"] = self._compress_tool_result(
                        msg["result"], tool=entry["tool"], args=entry["args"]
                    )
                elif method == "tools/list":
                    tools = msg["result"].get("tools")
                    if isinstance(tools, list):
                        self._inject_retrieve(tools)
                elif method == "initialize":
                    # 标准 MCP 的 initialize 结果不含 tools；个别 server 违例带上时同样注入
                    tools = msg["result"].get("tools")
                    if isinstance(tools, list):
                        self._inject_retrieve(tools)
        self._emit(msg)

    @staticmethod
    def _inject_retrieve(tools: list) -> None:
        if not any(isinstance(t, dict) and t.get("name") == "retrieve_original" for t in tools):
            tools.append(RETRIEVE_ORIGINAL_TOOL)

    def _compress_tool_result(self, result: dict, tool: str | None = None, args: dict | None = None) -> dict:
        if self.profile is None:
            return result
        # 窄意图调用（如指定 revision 的 git_show）：原样透传，不压缩不入库
        if self.profile.should_skip(tool, args):
            return result
        task_type = detect(list(self.context))
        contents = result.get("content")
        if not isinstance(contents, list):
            return result
        for item in contents:
            if not isinstance(item, dict) or item.get("type") != "text":
                continue
            text = item.get("text", "")
            try:
                try:
                    payload = json.loads(text)
                except (json.JSONDecodeError, ValueError):
                    payload = None
                if isinstance(payload, dict):
                    compressed = compress(payload, self.profile, task_type, store=self.store)
                    item["text"] = json.dumps(
                        compressed.compressed_payload, ensure_ascii=False
                    )
                elif self._has_output_text_rule(task_type):
                    wrapped = compress(
                        {"output": text}, self.profile, task_type, store=self.store
                    )
                    new_text = wrapped.compressed_payload.get("output")
                    if isinstance(new_text, str) and new_text != text:
                        item["text"] = (
                            f"{new_text}\n[sw-compressed; original: {wrapped.ref_id}"
                            " — call retrieve_original]"
                        )
            except Exception as exc:  # fail-open：压缩失败不影响代理可用性
                print(f"[sw-proxy] compress failed ({exc}); passthrough", file=sys.stderr)
        return result

    def _has_output_text_rule(self, task_type: TaskType) -> bool:
        task_name = task_type.value if isinstance(task_type, TaskType) else str(task_type)
        task_profile = self.profile.for_task(task_name) or self.profile.task_profiles.get(
            "fallback"
        )
        if task_profile is None:
            return False
        return any(rule.get("field") == "output" for rule in task_profile.text_rules)

    # ---------- 输出 ----------

    def _emit(self, msg: dict) -> None:
        self._emit_raw(json.dumps(msg, ensure_ascii=False))

    @staticmethod
    def _emit_raw(text: str) -> None:
        sys.stdout.write(text + "\n")
        sys.stdout.flush()  # R7：实时，不缓冲

    def _respond(self, msg_id: Any, result: dict | None, error: dict | None) -> None:
        msg: dict[str, Any] = {"jsonrpc": "2.0", "id": msg_id}
        if error is not None:
            msg["error"] = error
        else:
            msg["result"] = result
        self._emit(msg)

    async def _write_child(self, line: str) -> None:
        if self.proc and self.proc.stdin:
            self.proc.stdin.write(line.encode("utf-8") + b"\n")
            await self.proc.stdin.drain()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcp_compress_proxy.proxy",
        description="Task-aware transparent MCP compression proxy (stdio)",
        allow_abbrev=False,
    )
    parser.add_argument("--server", help="server 名（默认从子进程命令推断，匹配 profiles/*.yaml）")
    parser.add_argument("--db", default=None, help="SQLite ref 存储路径")
    parser.add_argument("--profiles-dir", default=None, help="Profile YAML 目录")
    parser.add_argument(
        "server_cmd",
        nargs=argparse.REMAINDER,
        help="'--' 之后的原始 MCP server 命令",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    # 全链路强制 UTF-8，避免 Windows 本地编码破坏 JSON
    for stream in (sys.stdin, sys.stdout):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = build_parser()
    args = parser.parse_args(argv)
    cmd = [a for a in (args.server_cmd or []) if a != "--"]
    if not cmd:
        parser.error("missing original MCP server command after '--'")
    asyncio.run(
        Proxy(cmd, server_name=args.server, db_path=args.db, profiles_dir=args.profiles_dir).run()
    )


if __name__ == "__main__":
    main()
