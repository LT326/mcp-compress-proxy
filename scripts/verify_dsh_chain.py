r"""端到端验证 DSH 接入链路（不需要启动 DSH）。

按 DSH 的启动方式拉起代理（同一 command/args、同一 MCP stdio 传输），用官方
MCP SDK（本仓库 .venv 里的 mcp 2.3.0，支持 2026-07-28 修订版）完成：

  1. initialize 握手（客户端会像 DSH 一样要求最新协议）
  2. tools/list 确认注入的 retrieve_original 存在
  3. tools/call git_log(max_count=60) 触发压缩，检查 sw-ref 标记
  4. tools/call retrieve_original 取回原文，检查 resultType == "complete"

用法：
    python scripts/verify_dsh_chain.py
    python scripts/verify_dsh_chain.py --repo E:\myrepo --max-count 80

退出码：0 通过；1 失败；2 环境/参数问题。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import dsh_common as dc  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="verify_dsh_chain.py",
        description="Verify the DSH -> mcp-compress-proxy -> MCP server chain",
        allow_abbrev=False,
    )
    parser.add_argument("--server", default="git", help="server 名（默认 git）")
    parser.add_argument("--repo", default=None, help="git 仓库路径（默认 .e2e/real-bench/real-repo）")
    parser.add_argument("--python", default=None, help="运行代理的 Python")
    parser.add_argument("--db", default=None, help="临时 sw-ref 数据库路径")
    parser.add_argument("--max-count", type=int, default=60, help="git_log 的 max_count（>20 才压缩）")
    return parser


def default_repo(root: Path) -> Path:
    demo = root / ".e2e" / "real-bench" / "real-repo"
    return demo if demo.exists() else root


def _text_of(result) -> str:
    return "\n".join(
        block.text for block in result.content if getattr(block, "type", "") == "text"
    )


async def run(args: argparse.Namespace) -> int:
    root = dc.REPO_ROOT
    python = Path(args.python) if args.python else dc.default_python(root)
    db = Path(args.db) if args.db else Path(os.environ.get("TEMP", "/tmp")) / "dsh-verify-refs.db"
    spec = dc.resolve_server(
        args.server, repo_root=root, git_repo=default_repo(root) if args.server == "git" else None
    )
    row = dc.build_row(spec, python=python, repo_root=root, db_path=db)
    config = row["config"]

    print(f"[1/5] spawn: {config['command']} {' '.join(map(str, config['args'][:6]))} ...")

    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
    except ImportError as exc:
        print(
            f"[dsh] error: missing MCP SDK ({exc}); run: .venv\\Scripts\\python.exe -m pip install -e .",
            file=sys.stderr,
        )
        return 2

    params = StdioServerParameters(
        command=config["command"],
        args=[str(a) for a in config["args"]],
        env=dict(os.environ),
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print(
                f"[2/5] initialize: protocol={init.protocol_version} "
                f"server={init.server_info.name} v{init.server_info.version}"
            )

            tools = await session.list_tools()
            names = [tool.name for tool in tools.tools]
            if "retrieve_original" not in names:
                print("[FAIL] retrieve_original 未出现在 tools/list 中", file=sys.stderr)
                return 1
            print(f"[3/5] tools/list: {len(names)} tools (retrieve_original injected)")

            if args.server != "git":
                print(f"[4/5] skip tool call for server {args.server!r}")
                print(f"[5/5] PASS (handshake + tools/list)")
                return 0

            repo = str(default_repo(root))
            result = await session.call_tool(
                "git_log", {"repo_path": repo, "max_count": args.max_count}
            )
            text = _text_of(result)
            match = re.search(r"sw-ref:[0-9a-f]+", text)
            if "sw-compressed" not in text or not match:
                print("[FAIL] git_log 结果没有压缩标记 / sw-ref", file=sys.stderr)
                print(text[:500], file=sys.stderr)
                return 1
            print(
                f"[4/5] git_log(max_count={args.max_count}): {len(text)} chars "
                f"~{len(text) // 4} tokens, ref={match.group(0)}"
            )

            restored = await session.call_tool("retrieve_original", {"ref_id": match.group(0)})
            original = _text_of(restored)
            result_type = getattr(restored, "result_type", None)
            if result_type != "complete":
                print(f"[FAIL] retrieve_original resultType={result_type!r}", file=sys.stderr)
                return 1
            if len(original) <= len(text):
                print(
                    f"[FAIL] retrieve 没有比压缩结果更长 ({len(original)} <= {len(text)})",
                    file=sys.stderr,
                )
                return 1
            ratio = len(original) / max(1, len(text))
            print(
                f"[5/5] retrieve_original: {len(original)} chars "
                f"~{len(original) // 4} tokens (x{ratio:.1f} vs compressed), "
                f"resultType={result_type}"
            )
            if len(original.splitlines()) <= 1:
                print("[FAIL] retrieve_original 返回内容为空/单行", file=sys.stderr)
                return 1

    print("PASS: DSH chain (spawn -> MCP handshake -> compression -> reversible retrieve)")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(run(args))
    except dc.DshConfigError as exc:
        print(f"[dsh] error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
