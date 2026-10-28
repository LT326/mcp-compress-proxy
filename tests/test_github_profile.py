"""GitHub MCP profile 回归：真实 v2.0.3 输出形状 + compress_only_tools 语义。"""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_compress_proxy.compressor.engine import compress
from mcp_compress_proxy.profiles.loader import load_profile
from mcp_compress_proxy.store.sqlite_store import SqliteStore, retrieve_original
from mcp_compress_proxy.task_context.detector import TaskType

PROFILES = Path(__file__).resolve().parents[1] / "profiles"


@pytest.fixture()
def store(tmp_path):
    s = SqliteStore(tmp_path / "refs.db")
    yield s
    s.close()


def _body(i: int, lines: int = 40) -> str:
    return "\n".join(
        [f"### issue {i} body", "template line"] + [f"detail line {j} " + "x" * 40 for j in range(lines)]
    )


def _issues_payload(n: int = 20) -> dict:
    return {
        "issues": [
            {
                "number": 1000 + i,
                "title": f"bug: thing {i} broke",
                "body": _body(i),
                "state": "OPEN",
                "user": {"login": f"user{i}"},
                "labels": [{"name": "bug"}],
                "comments": i,
                "created_at": "2026-10-01T00:00:00Z",
                "updated_at": "2026-10-02T00:00:00Z",
            }
            for i in range(n)
        ]
    }


def _commits_payload(n: int = 20) -> list[dict]:
    return [
        {
            "sha": f"{i:040x}",
            "html_url": f"https://github.com/o/r/commit/{i}",
            "commit": {"message": f"commit {i} subject\n\nlong body " + "y" * 200},
            "author": {"login": f"dev{i}"},
            "committer": {"login": f"dev{i}"},
        }
        for i in range(n)
    ]


def _repos_payload(n: int = 10) -> dict:
    return {
        "total_count": 69713,
        "incomplete_results": False,
        "items": [
            {
                "full_name": f"org/repo{i}",
                "description": "a repository " + "z" * 120,
                "language": "Python",
                "stargazers_count": 100 + i,
                "forks_count": i,
                "open_issues_count": 3,
                "html_url": f"https://github.com/org/repo{i}",
            }
            for i in range(n)
        ],
    }


def test_github_only_whitelisted_tools_are_compressed():
    profile = load_profile("github", PROFILES)
    for tool in ("list_issues", "search_issues", "list_pull_requests", "list_commits", "search_repositories"):
        assert profile.should_compress_tool(tool), tool
    for tool in ("get_me", "create_issue", "issue_read"):
        assert not profile.should_compress_tool(tool), tool
    # pull_request_read 是 text_only：JSON 形态透传，diff 文本才压缩
    assert profile.should_compress_tool("pull_request_read")
    assert profile.is_text_only("pull_request_read")


def test_github_issues_debug_keeps_head_of_body(store):
    profile = load_profile("github", PROFILES)
    payload = _issues_payload()
    result = compress(payload, profile, TaskType.DEBUG, store=store, tool="list_issues")
    issues = result.compressed_payload["issues"]
    assert len(issues) == 20
    assert issues[0]["number"] == 1000
    assert issues[0]["user"]["login"] == "user0"
    body = issues[0]["body"]
    assert "lines omitted (sw-compressed)" in body
    assert body.splitlines()[0] == "### issue 0 body"
    assert len(body.splitlines()) <= 32
    assert retrieve_original(store, result.ref_id)["value"] == payload


def test_github_review_keeps_body_first_line(store):
    profile = load_profile("github", PROFILES)
    payload = _issues_payload()
    result = compress(payload, profile, TaskType.REVIEW, store=store, tool="search_issues")
    issue = result.compressed_payload["issues"][0]
    assert issue["body"].splitlines()[0] == "### issue 0 body"
    assert "lines omitted" in issue["body"]
    assert "labels" in issue


def test_github_explore_drops_bodies(store):
    profile = load_profile("github", PROFILES)
    payload = _issues_payload()
    result = compress(payload, profile, TaskType.EXPLORE, store=store, tool="list_issues")
    issue = result.compressed_payload["issues"][0]
    assert "body" not in issue
    assert "detail line" not in str(result.compressed_payload)
    assert issue["title"].startswith("bug:")


def test_github_fallback_bounds_items_and_body(store):
    profile = load_profile("github", PROFILES)
    payload = _issues_payload()
    result = compress(payload, profile, TaskType.UNKNOWN, store=store, tool="list_issues")
    issues = result.compressed_payload["issues"]
    assert len(issues) == 3
    assert issues[0]["body"].splitlines()[0] == "### issue 0 body"
    assert "detail line" not in str(result.compressed_payload)


def test_github_top_level_array_uses_rows_envelope(store):
    profile = load_profile("github", PROFILES)
    payload = _commits_payload()
    result = compress(payload, profile, TaskType.REVIEW, store=store, tool="list_commits")
    rows = result.compressed_payload["rows"]
    assert result.compressed_payload["sw_ref"].startswith("sw-ref:")
    assert rows[0]["commit"]["message"].splitlines()[0] == "commit 0 subject"
    assert rows[0]["sha"] == payload[0]["sha"]
    assert retrieve_original(store, result.ref_id)["value"] == payload


def test_github_repo_search_keeps_metadata_not_description_noise(store):
    profile = load_profile("github", PROFILES)
    payload = _repos_payload()
    result = compress(payload, profile, TaskType.UNKNOWN, store=store, tool="search_repositories")
    items = result.compressed_payload["items"]
    assert len(items) == 3
    assert items[0]["full_name"] == "org/repo0"
    assert "description" not in items[0]