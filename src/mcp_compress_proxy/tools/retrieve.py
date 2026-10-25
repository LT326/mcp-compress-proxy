"""注入给 MCP 客户端的 retrieve_original 工具：定义 + 代理本地处理（不转发子进程）。"""

from __future__ import annotations

import json

from ..store.sqlite_store import SqliteStore, retrieve_original

RETRIEVE_ORIGINAL_TOOL = {
    "name": "retrieve_original",
    "description": (
        "Retrieve the full original payload (or a specific field) that was compressed away. "
        "Supports partial retrieval to save tokens: pass 'lines' (e.g. '120-180') for a line "
        "range, or 'grep' (regex, case-insensitive) to get only matching lines with line numbers."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "ref_id": {"type": "string", "description": "The sw-ref:... identifier"},
            "field_path": {
                "type": "string",
                "description": "Optional dot-path to a specific field, e.g. 'commits.5.diff'",
            },
            "lines": {
                "type": "string",
                "description": "Optional 1-based inclusive line range of the original text, e.g. '120-180'",
            },
            "grep": {
                "type": "string",
                "description": "Optional regex; returns only matching lines, prefixed with line numbers",
            },
        },
        "required": ["ref_id"],
    },
}


def handle_retrieve(store: SqliteStore, arguments: dict | None) -> tuple[dict | None, dict | None]:
    """返回 (result, error)，恰好一个为 None。

    - ref_id 缺失 → JSON-RPC error
    - field_path/lines/grep 缺失 → 完整原始载荷
    - field_path 无效 → error，message 含 "path not found"
    """
    args = arguments or {}
    outcome = retrieve_original(
        store,
        args.get("ref_id"),
        field_path=args.get("field_path"),
        lines=args.get("lines"),
        grep=args.get("grep"),
    )
    if not outcome["ok"]:
        message = outcome["error"]
        code = -32001 if "ref not found" in message else -32602
        return None, {"code": code, "message": message}

    value = outcome["value"]
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
    # MCP 2026-07-28：tools/call 结果必须带 resultType（早期修订版缺省视为 complete，
    # 但 2026-07-28 客户端会直接拒绝缺失结果 —— ZCode 实测踩中）
    result = {
        "resultType": "complete",
        "content": [{"type": "text", "text": text}],
        "isError": False,
    }
    return result, None
