"""P0 优化测试：意图感知跳过（skip_compression_if）、部分取回（lines/grep）、
commit_digest 块级摘要。"""

import json

from mcp_compress_proxy.compressor.engine import compress
from mcp_compress_proxy.compressor.text_rules import compress_text
from mcp_compress_proxy.profiles.loader import Profile, RetainRule, TaskProfile, load_profile
from mcp_compress_proxy.store.sqlite_store import SqliteStore, retrieve_original
from mcp_compress_proxy.task_context.detector import TaskType

PROJECT_PROFILES = __import__("pathlib").Path(__file__).resolve().parents[1] / "profiles"

GIT_LOG_TEXT = "\n".join(
    ["Commit history:"]
    + [
        line
        for i in range(5)
        for line in (
            f"Commit: {'a' * 39}{i}",
            "Author: dev <dev@example.com>",
            "Date: 2026-10-01T10:00:00+08:00",
            f"Message: subject {i}",
            "  body line",
            "",
        )
    ]
)


# ---------------- P0-1 意图感知跳过 ----------------

def test_git_profile_skip_rules_loaded_and_matched():
    profile = load_profile("git", PROJECT_PROFILES)
    assert profile.should_skip("git_show", {"revision": "abc"}) is True
    assert profile.should_skip("git_diff", {"target": "abc"}) is True
    assert profile.should_skip("git_log", {"max_count": 20}) is True
    assert profile.should_skip("git_log", {"max_count": 21}) is False
    assert profile.should_skip("git_log", {}) is False          # 参数缺省不跳过
    assert profile.should_skip("git_status", {}) is False
    assert profile.should_skip(None, None) is False


def test_should_skip_args_le_requires_numeric():
    profile = Profile(server="git", skip_rules=[])
    from mcp_compress_proxy.profiles.loader import SkipRule

    profile.skip_rules = [SkipRule(tool="git_log", args_le={"max_count": 20})]
    assert profile.should_skip("git_log", {"max_count": "many"}) is False  # 非数值不匹配
    assert profile.should_skip("git_log", {"max_count": 5}) is True


# ---------------- P0-2 部分取回 ----------------

def test_partial_retrieve_lines_and_grep(tmp_path):
    store = SqliteStore(tmp_path / "refs.db")
    text = "\n".join(f"line {i}" for i in range(1, 101))
    store.save("sw-ref:partial01", "git", json.dumps({"output": text}), None)

    # output 自动解包：全量取回是纯文本而非 {"output": ...}
    full = retrieve_original(store, "sw-ref:partial01")
    assert full["ok"] and full["value"].startswith("line 1\n")

    seg = retrieve_original(store, "sw-ref:partial01", lines="10-12")
    assert seg["value"] == "line 10\nline 11\nline 12"

    hit = retrieve_original(store, "sw-ref:partial01", grep="^line 4$")
    assert hit["value"] == "4: line 4"  # 带行号前缀，精确匹配一行

    bad = retrieve_original(store, "sw-ref:partial01", lines="500-600")
    assert not bad["ok"] and "out of range" in bad["error"]
    no_hit = retrieve_original(store, "sw-ref:partial01", grep="no-such-token")
    assert not no_hit["ok"] and "no lines match" in no_hit["error"]


# ---------------- P0-3 commit_digest ----------------

def test_commit_digest_compresses_log_blocks():
    rule = {"mode": "commit_digest", "fallback": {"head": 30, "tail": 10}}
    out = compress_text(rule, GIT_LOG_TEXT, always_preserve=False)
    lines = out.splitlines()
    assert len(lines) == 5  # 每条 commit 一行
    assert "Date:" not in out and "body line" not in out  # 日期与正文被丢
    for i in range(5):
        assert f"{'a' * 39}{i}" in out and f"subject {i}" in out


def test_commit_digest_falls_back_to_head_tail_for_non_log_text():
    rule = {"mode": "commit_digest", "fallback": {"head": 2, "tail": 2}}
    text = "\n".join(f"plain {i}" for i in range(20))
    out = compress_text(rule, text, always_preserve=False)
    assert "plain 0" in out and "plain 19" in out
    assert "plain 10" not in out  # 中段省略（head_tail 回退生效）


def test_engine_digest_via_profile_and_marker(tmp_path):
    profile = load_profile("git", PROJECT_PROFILES)
    store = SqliteStore(tmp_path / "refs.db")
    result = compress({"output": GIT_LOG_TEXT}, profile, TaskType.UNKNOWN, store=store)
    # fallback 档白名单不含 output 之外的键；text_rules 写回 digest
    assert "subject 3" in result.compressed_payload["output"]
    assert "Date:" not in result.compressed_payload["output"]
    # 取回（解包后）仍是完整原文
    restored = retrieve_original(store, result.ref_id)
    assert "Date: 2026-10-01" in restored["value"]
