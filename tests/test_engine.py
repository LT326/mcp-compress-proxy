"""阶段二测试 4—6：Profile 驱动的压缩引擎 + SQLite 可逆检索。"""

import json

from mcp_compress_proxy.compressor.engine import compress
from mcp_compress_proxy.profiles.loader import Profile, RetainRule, TaskProfile
from mcp_compress_proxy.store.sqlite_store import SqliteStore, retrieve_original
from mcp_compress_proxy.task_context.detector import TaskType


def _git_payload() -> dict:
    return {
        "repository": "demo/repo",
        "total": 5,
        "commits": [
            {
                "hash": f"c{i:040x}",
                "author": f"dev{i}@example.com",
                "message": f"commit {i}: fix the parser bug " * (4 if i == 0 else 1),
                "diff_stat": f" {i + 1} files changed, {10 * (i + 1)} insertions(+)",
                "tree": "t" * 40,
                "parent_hashes": ["p" * 40],
            }
            for i in range(5)
        ],
    }


def _debug_profile() -> Profile:
    return Profile(
        server="git",
        task_profiles={
            "debug": TaskProfile(
                retain=[
                    RetainRule("commits[0:3].message", "critical"),
                    RetainRule("commits[0:3].diff_stat", "high"),
                    RetainRule("commits[0:3].hash"),
                ],
                drop=["commits[3:].message", "commits[*].tree", "commits[*].parent_hashes"],
            )
        },
    )


def test_4_compress_debug_keeps_first_three_commit_messages(tmp_path):
    payload = _git_payload()
    store = SqliteStore(tmp_path / "refs.db")
    result = compress(payload, _debug_profile(), TaskType.DEBUG, store=store)

    commits = result.compressed_payload["commits"]
    assert len(commits) == 3
    for i in range(3):
        assert commits[i]["message"] == payload["commits"][i]["message"]
        assert commits[i]["diff_stat"] == payload["commits"][i]["diff_stat"]
        assert commits[i]["hash"] == payload["commits"][i]["hash"]

    serialized = json.dumps(result.compressed_payload, ensure_ascii=False)
    assert "commit 4" not in serialized  # commits[3:] 的 message 被丢弃
    assert '"tree"' not in serialized  # commits[*].tree 被丢弃
    assert "p" * 40 not in serialized  # parent_hashes 被丢弃
    assert result.compressed_payload["sw_ref"].startswith("sw-ref:")
    assert "commits[4].message" in result.dropped_paths


def test_5_retrieve_original_restores_full_payload(tmp_path):
    payload = _git_payload()
    store = SqliteStore(tmp_path / "refs.db")
    result = compress(payload, _debug_profile(), TaskType.DEBUG, store=store)

    full = retrieve_original(store, result.ref_id)
    assert full["ok"] is True
    assert full["value"] == payload  # 完整原始 payload 可还原

    sub = retrieve_original(store, result.ref_id, "commits.4.message")
    assert sub["ok"] is True
    assert sub["value"] == payload["commits"][4]["message"]


def test_6_unknown_task_falls_back_with_over_half_compression(tmp_path):
    payload = {"meta": "info", "items": [{"id": i, "blob": "x" * 60} for i in range(100)]}
    store = SqliteStore(tmp_path / "refs.db")
    result = compress(payload, _debug_profile(), TaskType.UNKNOWN, store=store)

    # 回退策略：顶层键保留，数组只留前 3 项
    assert result.compressed_payload["meta"] == "info"
    assert len(result.compressed_payload["items"]) == 3

    original_chars = len(json.dumps(payload, ensure_ascii=False))
    compressed_chars = len(json.dumps(result.compressed_payload, ensure_ascii=False))
    assert 1 - compressed_chars / original_chars > 0.5
    assert result.transform_manifest["strategy"] == "generic_fallback"
