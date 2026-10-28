"""压缩引擎：compress(payload, profile, task_type) -> CompressedResult。

语义（白名单模式）：task profile 只要有 retain 规则，压缩结果就只包含
retain 命中的路径；其余一律视为 dropped（可经 sw-ref 取回）。
task_type 无对应 profile 时：优先用 profile 内声明的 fallback 任务档，
否则走通用回退（顶层键全保留、数组只留前 3 项）。
"""

from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..profiles.loader import Profile, TaskProfile
from ..store.sqlite_store import SqliteStore
from ..task_context.detector import TaskType
from . import paths, text_rules

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def default_db_path() -> Path:
    return Path(os.environ.get("MCP_COMPRESS_DB", PROJECT_ROOT / ".sw_refs.db"))


@dataclass
class CompressedResult:
    compressed_payload: dict
    ref_id: str
    dropped_paths: list[str]
    original_token_estimate: int
    transform_manifest: dict = field(default_factory=dict)


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def make_ref_id(payload: Any) -> str:
    digest = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()[:12]
    return f"sw-ref:{digest}"


def _fallback_compress(payload: dict) -> dict:
    """通用回退：保留顶层键；数组只留前 3 项，其余入 ref。"""
    out: dict = {}
    for key, value in payload.items():
        out[key] = deepcopy(value[:3]) if isinstance(value, list) else deepcopy(value)
    return out


def _whitelist_build(payload: dict, task_profile: TaskProfile) -> dict:
    out: dict = {}
    for rule in task_profile.retain:
        for concrete, value in paths.extract(payload, rule.path):
            paths.set_path(out, concrete, value)
    return out


def rule_applies(rule: dict, tool: str | None) -> bool:
    """工具级作用域（YAML 声明）：tools / exclude_tools。

    tool 未知（如 benchmark 直接调用 compress）时不做限制，保持旧行为。
    """
    if tool is not None:
        tools = rule.get("tools")
        if tools is not None and tool not in tools:
            return False
        exclude = rule.get("exclude_tools")
        if exclude and tool in exclude:
            return False
    return True


def _apply_text_rules(
    payload: dict,
    compressed: dict,
    profile: Profile,
    task_profile: TaskProfile,
    tool: str | None = None,
) -> None:
    """text_rules 的 field 支持路径表达式：content / stdout / commits[*].message 等。

    规则从原始 payload 取值，压缩后写回 compressed（None = 整字段丢弃）。
    text_rule 可选 tools / exclude_tools，用于同一 server 内按工具区分策略
    （例如 filesystem 的 read_text_file 与 list_directory 输出形态完全不同）。
    """
    for rule in task_profile.text_rules:
        field = rule.get("field")
        if not field:
            continue
        if not rule_applies(rule, tool):
            continue
        for concrete, value in paths.extract(payload, field):
            if not isinstance(value, str):
                continue
            new_text = text_rules.compress_text(rule, value, profile.always_preserve_error_signals)
            if new_text is None:
                paths.remove_path(compressed, concrete)
            else:
                paths.set_path(compressed, concrete, new_text)


def _format_leaf(leaf_path: tuple, envelope: str | None) -> str:
    """数组信封内的路径去掉信封前缀（rows[0].id → [0].id），与原始数组对齐。"""
    formatted = paths.format_path(leaf_path)
    if envelope and formatted.startswith(envelope):
        formatted = formatted[len(envelope):].lstrip(".")
    return formatted


def compress(
    payload: dict | list,
    profile: Profile,
    task_type: TaskType | str,
    store: SqliteStore | None = None,
    tool: str | None = None,
    ref_payload: dict | list | None = None,
) -> CompressedResult:
    """按 Profile 压缩 payload；返回（含 sw_ref 的）压缩结果。

    数组载荷：profile.array_envelope 声明后，payload 会包成 {"<envelope>": [...]}
    再套 retain 规则，输出仍是该信封对象（数组本身放不下 sw_ref）。
    ref_payload：要写入 SQLite 的原始数据；None 表示就是 payload。文本载荷
    （如 sqlite 的 Python repr）用它保存原文，保证 retrieve 逐字节还原。
    """
    task_name = task_type.value if isinstance(task_type, TaskType) else str(task_type)
    raw_payload = payload
    envelope = profile.array_envelope
    enveloped = False
    if isinstance(payload, list):
        if not envelope:
            raise ValueError(
                "list payload requires profile.array_envelope to be declared in YAML"
            )
        payload = {envelope: payload}
        enveloped = True
    original_payload = ref_payload if ref_payload is not None else raw_payload
    original_json = _canonical(original_payload)
    ref_id = make_ref_id(original_payload)

    task_profile = profile.for_task(task_name)
    strategy = "whitelist"
    if task_profile is None and "fallback" in profile.task_profiles:
        task_profile = profile.task_profiles["fallback"]
        strategy = "declared_fallback"
    if task_profile is None:
        compressed = _fallback_compress(payload)
        strategy = "generic_fallback"
    else:
        compressed = _whitelist_build(payload, task_profile)
        _apply_text_rules(payload, compressed, profile, task_profile, tool=tool)

    # dropped = 原始叶子在压缩结果中取不到、或取到的值已改变
    dropped_paths: list[str] = []
    total_leaves = 0
    for leaf_path, original_value in paths.iter_leaves(payload):
        total_leaves += 1
        ok, current = paths.get_at(compressed, leaf_path)
        if not ok or current != original_value:
            dropped_paths.append(_format_leaf(leaf_path, envelope if enveloped else None))

    compressed["sw_ref"] = ref_id

    manifest = {
        "ref_id": ref_id,
        "server": profile.server,
        "task_type": task_name,
        "strategy": strategy,
        "total_leaf_count": total_leaves,
        "dropped_path_count": len(dropped_paths),
        "dropped_paths": dropped_paths,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    if store is None:
        store = SqliteStore(default_db_path())
    store.save(ref_id, profile.server, original_json, manifest)

    return CompressedResult(
        compressed_payload=compressed,
        ref_id=ref_id,
        dropped_paths=dropped_paths,
        original_token_estimate=len(original_json) // 4,
        transform_manifest=manifest,
    )