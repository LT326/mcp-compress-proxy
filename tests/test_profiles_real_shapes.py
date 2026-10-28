"""真实 server 输出形状的 Profile 回归：filesystem 文本输出 + memory 知识图谱。

背景：filesystem 的 v1.0 profile 按合成 JSON 形状写，真实 server 返回的是
"[DIR] name" 文本，压缩从未触发。v1.1 用 text_rules.tools 作用域对齐真实输出；
memory 的 read_graph 返回 {entities, relations} JSON，可走 retain 白名单。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_compress_proxy.compressor.engine import compress, rule_applies
from mcp_compress_proxy.profiles.loader import load_profile
from mcp_compress_proxy.store.sqlite_store import SqliteStore, retrieve_original
from mcp_compress_proxy.task_context.detector import TaskType

PROFILES = Path(__file__).resolve().parents[1] / "profiles"


@pytest.fixture()
def store(tmp_path):
    s = SqliteStore(tmp_path / "refs.db")
    yield s
    s.close()


def _fs_listing(n: int = 200) -> str:
    lines = []
    for i in range(n):
        kind = "[DIR]" if i % 4 == 0 else "[FILE]"
        lines.append(f"{kind} src/module_{i % 17}/part_{i}.py")
    return "\n".join(lines)


def _fs_source(n: int = 3000, error_line: int | None = 500) -> str:
    lines = []
    for i in range(n):
        if error_line is not None and i == error_line:
            lines.append('    raise RuntimeError("unexpected state in phase transition")')
        elif i % 50 == 0:
            lines.append(f"def handler_{i}(request, context):")
        else:
            lines.append(f"    value_{i % 50} = transform(payload_{i % 7}, offset={i})")
    return "\n".join(lines)


def test_filesystem_listing_compresses_with_tool_scope(store):
    profile = load_profile("filesystem", PROFILES)
    text = _fs_listing(200)
    result = compress(
        {"output": text}, profile, TaskType.EXPLORE, store=store, tool="list_directory"
    )
    out = result.compressed_payload["output"]
    assert "lines omitted (sw-compressed)" in out
    assert out.startswith("[DIR] src/module_0/part_0.py")
    assert len(out.splitlines()) < len(text.splitlines())
    assert retrieve_original(store, result.ref_id)["value"] == text


def test_filesystem_read_uses_different_rule_than_listing(store):
    profile = load_profile("filesystem", PROFILES)
    text = _fs_source(1200, error_line=None)
    as_file = compress(
        {"output": text}, profile, TaskType.EXPLORE, store=store, tool="read_text_file"
    ).compressed_payload["output"]
    as_list = compress(
        {"output": text}, profile, TaskType.EXPLORE, store=store, tool="list_directory"
    ).compressed_payload["output"]
    # read_text_file: head 60 / tail 20；list_directory: head 100 / tail 20
    assert len(as_file.splitlines()) < len(as_list.splitlines())


def test_filesystem_debug_keeps_error_context(store):
    profile = load_profile("filesystem", PROFILES)
    text = _fs_source(3000, error_line=500)
    result = compress(
        {"output": text}, profile, TaskType.DEBUG, store=store, tool="read_text_file"
    )
    out = result.compressed_payload["output"]
    assert 'raise RuntimeError("unexpected state in phase transition")' in out
    assert "def handler_500" in out or "value_" in out  # 错误前后文一并保留
    assert len(out.splitlines()) < len(text.splitlines()) / 10


def test_filesystem_error_rule_falls_back_when_no_error(store):
    profile = load_profile("filesystem", PROFILES)
    text = _fs_source(3000, error_line=None)
    result = compress(
        {"output": text}, profile, TaskType.DEBUG, store=store, tool="read_text_file"
    )
    out = result.compressed_payload["output"]
    lines = out.splitlines()
    assert lines[0].startswith("def handler_0")  # fallback head 40
    assert "offset=2999" in out  # fallback tail 20


def test_filesystem_skips_structural_tools():
    profile = load_profile("filesystem", PROFILES)
    assert profile.should_skip("get_file_info", {"path": "x"})
    assert profile.should_skip("write_file", {"path": "x", "content": "y"})
    assert not profile.should_skip("read_text_file", {"path": "x"})


def _memory_payload(entities: int = 20, observations: int = 8) -> dict:
    return {
        "entities": [
            {
                "name": f"svc_{i}",
                "entityType": "service",
                "observations": [
                    f"observation {j} of svc_{i}: " + "detail text " * 8
                    for j in range(observations)
                ],
            }
            for i in range(entities)
        ],
        "relations": [
            {
                "from": f"svc_{i}",
                "to": f"svc_{(i + 1) % entities}",
                "relationType": "depends_on",
            }
            for i in range(entities)
        ],
    }


def test_memory_explore_keeps_graph_skeleton_only(store):
    profile = load_profile("memory", PROFILES)
    payload = _memory_payload()
    result = compress(payload, profile, TaskType.EXPLORE, store=store, tool="read_graph")
    compressed = result.compressed_payload
    assert len(compressed["entities"]) == 20
    assert compressed["entities"][0]["name"] == "svc_0"
    assert "observations" not in compressed["entities"][0]
    assert len(compressed["relations"]) == 20
    assert "detail text" not in str(compressed)
    assert retrieve_original(store, result.ref_id)["value"] == payload


def test_memory_debug_keeps_first_four_observations(store):
    profile = load_profile("memory", PROFILES)
    payload = _memory_payload()
    result = compress(payload, profile, TaskType.DEBUG, store=store, tool="read_graph")
    observations = result.compressed_payload["entities"][0]["observations"]
    assert len(observations) == 4
    assert observations[0] == payload["entities"][0]["observations"][0]
    assert result.compressed_payload["relations"][0]["relationType"] == "depends_on"


def test_memory_fallback_bounds_entities_and_observations(store):
    profile = load_profile("memory", PROFILES)
    payload = _memory_payload()
    result = compress(payload, profile, TaskType.UNKNOWN, store=store, tool="read_graph")
    compressed = result.compressed_payload
    assert len(compressed["entities"]) == 3
    assert len(compressed["entities"][0]["observations"]) == 2
    assert len(compressed["relations"]) == 3


def test_memory_write_tools_are_skipped():
    profile = load_profile("memory", PROFILES)
    assert profile.should_skip("create_entities", {"entities": []})
    assert not profile.should_skip("read_graph", {})


def test_rule_applies_scope_semantics():
    rule = {"tools": ["read_text_file", "read_file"]}
    assert rule_applies(rule, "read_text_file")
    assert not rule_applies(rule, "list_directory")
    assert rule_applies(rule, None)  # tool 未知时不限制（benchmark 旧行为）
    excluded = {"exclude_tools": ["list_directory"]}
    assert not rule_applies(excluded, "list_directory")
    assert rule_applies(excluded, "read_text_file")
