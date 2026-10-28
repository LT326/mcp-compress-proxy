"""数组载荷 / Python repr 载荷 / sqlite profile 的回归测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_compress_proxy.compressor.engine import compress
from mcp_compress_proxy.profiles.loader import Profile, RetainRule, TaskProfile, load_profile
from mcp_compress_proxy.store.sqlite_store import SqliteStore, retrieve_original
from mcp_compress_proxy.task_context.detector import TaskType

PROFILES = Path(__file__).resolve().parents[1] / "profiles"


@pytest.fixture()
def store(tmp_path):
    s = SqliteStore(tmp_path / "refs.db")
    yield s
    s.close()


def _rows(n: int = 60) -> list[dict]:
    return [{"id": i, "name": f"item{i}", "payload": "x" * 40} for i in range(n)]


def _array_profile(keep: int) -> Profile:
    return Profile(
        server="sqlite",
        array_envelope="rows",
        task_profiles={
            "fallback": TaskProfile(retain=[RetainRule(f"rows[0:{keep}]", "critical")])
        },
    )


def test_array_envelope_compresses_and_stores_original_array(store):
    payload = _rows(10)
    result = compress(payload, _array_profile(3), TaskType.UNKNOWN, store=store)
    assert [row["id"] for row in result.compressed_payload["rows"]] == [0, 1, 2]
    assert result.compressed_payload["sw_ref"].startswith("sw-ref:")
    # dropped_paths 去掉信封前缀，与原始数组下标对齐
    assert "[3].id" in result.dropped_paths
    assert retrieve_original(store, result.ref_id)["value"] == payload
    # 数组内的子字段可取回（numeric path），不必拉全量
    assert retrieve_original(store, result.ref_id, "5.name")["value"] == "item5"


def test_array_payload_without_envelope_raises(store):
    profile = Profile(server="x")
    with pytest.raises(ValueError):
        compress([{"a": 1}], profile, TaskType.UNKNOWN, store=store)


def test_ref_payload_keeps_python_repr_text(store):
    """sqlite 的 Python repr：压缩用解析后的结构，ref 里存原始文本。"""
    text = str(_rows(10))
    parsed = [{"id": i} for i in range(10)]  # 模拟 literal_eval 结果
    result = compress(
        {"rows": parsed},
        _array_profile(2),
        TaskType.UNKNOWN,
        store=store,
        ref_payload={"output": text},
    )
    assert len(result.compressed_payload["rows"]) == 2
    assert retrieve_original(store, result.ref_id)["value"] == text
    # 文本包装仍支持字段路径：内部按 Python repr 解析后取值
    assert retrieve_original(store, result.ref_id, "5.name")["value"] == "item5"


def test_sqlite_profile_truncates_rows_per_task(store):
    profile = load_profile("sqlite", PROFILES)
    payload = _rows(120)
    debug = compress(payload, profile, TaskType.DEBUG, store=store)
    review = compress(payload, profile, TaskType.REVIEW, store=store)
    explore = compress(payload, profile, TaskType.EXPLORE, store=store)
    fallback = compress(payload, profile, TaskType.UNKNOWN, store=store)
    assert len(debug.compressed_payload["rows"]) == 30
    assert len(review.compressed_payload["rows"]) == 100
    assert len(explore.compressed_payload["rows"]) == 50
    assert len(fallback.compressed_payload["rows"]) == 50
    assert retrieve_original(store, debug.ref_id)["value"] == payload


def test_sqlite_profile_skips_schema_tools():
    profile = load_profile("sqlite", PROFILES)
    assert profile.array_envelope == "rows"
    assert profile.parse_python_literals is True
    for tool in ("list_tables", "describe_table", "create_table", "append_insight"):
        assert profile.should_skip(tool, {}), tool
    assert not profile.should_skip("read_query", {"query": "select 1"})


def test_loader_reads_array_settings_from_yaml(tmp_path):
    path = tmp_path / "demo.yaml"
    path.write_text(
        "server: demo\narray_envelope: items\nparse_python_literals: true\n"
        "task_profiles:\n  fallback:\n    retain: [{path: 'items[0:1]'}]\n",
        encoding="utf-8",
    )
    from mcp_compress_proxy.profiles.loader import load_profile

    profile = load_profile("demo", tmp_path)
    assert profile.array_envelope == "items"
    assert profile.parse_python_literals is True