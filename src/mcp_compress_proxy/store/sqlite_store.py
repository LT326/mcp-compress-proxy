"""SQLite 可逆存储：ref_id → 原始 payload + transform manifest。

同步 sqlite3 实现（引擎与测试直接使用；代理异步侧在 executor 中调用）。
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS refs (
    ref_id TEXT PRIMARY KEY,
    server TEXT NOT NULL,
    original_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS transform_manifest (
    ref_id TEXT PRIMARY KEY,
    manifest_json TEXT NOT NULL
);
"""


class SqliteStore:
    def __init__(self, db_path: Path | str):
        self.db_path = str(db_path)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def save(self, ref_id: str, server: str, original_json: str, manifest: dict | None = None) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO refs (ref_id, server, original_json, created_at) VALUES (?, ?, ?, ?)",
            (ref_id, server, original_json, (manifest or {}).get("created_at", "")),
        )
        if manifest is not None:
            self._conn.execute(
                "INSERT OR REPLACE INTO transform_manifest (ref_id, manifest_json) VALUES (?, ?)",
                (ref_id, json.dumps(manifest, ensure_ascii=False)),
            )
        self._conn.commit()

    def load(self, ref_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT original_json FROM refs WHERE ref_id = ?", (ref_id,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def manifest(self, ref_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT manifest_json FROM transform_manifest WHERE ref_id = ?", (ref_id,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def close(self) -> None:
        self._conn.close()


def resolve_field(payload: Any, field_path: str) -> tuple[bool, Any]:
    """按点分路径取子字段；纯数字段视为数组下标（支持 commits.4.diff 写法）。"""
    node = payload
    for part in field_path.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif (
            isinstance(node, list)
            and part.lstrip("-").isdigit()
            and -len(node) <= int(part) < len(node)
        ):
            node = node[int(part)]
        else:
            return False, None
    return True, node


def retrieve_original(
    store: SqliteStore,
    ref_id: str,
    field_path: str | None = None,
    lines: str | None = None,
    grep: str | None = None,
) -> dict:
    """取回入口：返回 {"ok": bool, "value" | "error": ...}，供代理与测试共用。

    - field_path：JSON 点分路径（子字段）
    - lines："start-end"（1 起含端），仅取该行区间 —— 部分取回，省 token
    - grep：正则，仅返回命中行（带行号前缀，便于二次定位）
    - 纯文本包装载荷（{"output": text}）自动解包
    """
    if not ref_id:
        return {"ok": False, "error": "missing required argument: ref_id"}
    payload = store.load(ref_id)
    if payload is None:
        return {"ok": False, "error": f"ref not found: {ref_id}"}
    value = payload
    if field_path:
        ok, value = resolve_field(payload, field_path)
        if not ok:
            return {"ok": False, "error": f"path not found: {field_path}"}
    if isinstance(value, dict) and set(value.keys()) == {"output"} and isinstance(value["output"], str):
        value = value["output"]  # 代理的 {"output": text} 文本包装 → 解包

    if grep:
        try:
            pattern = re.compile(grep, re.IGNORECASE)
        except re.error as exc:
            return {"ok": False, "error": f"invalid grep pattern: {exc}"}
        matched = [f"{i + 1}: {l}" for i, l in enumerate(str(value).splitlines()) if pattern.search(l)]
        if not matched:
            return {"ok": False, "error": f"grep: no lines match {grep!r}"}
        value = "\n".join(matched)
    elif lines:
        m = re.match(r"^(\d+)-(\d+)$", str(lines).strip())
        if not m:
            return {"ok": False, "error": "lines must look like 'start-end' (1-based, inclusive)"}
        start, end = int(m.group(1)), int(m.group(2))
        all_lines = str(value).splitlines()
        if start < 1 or end < start or start > len(all_lines):
            return {"ok": False, "error": f"lines out of range (1-{len(all_lines)})"}
        value = "\n".join(all_lines[start - 1 : end])
    return {"ok": True, "value": value}
