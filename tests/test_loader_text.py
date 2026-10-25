"""补充测试：Profile YAML 加载器 + 文本规则（error_anchor / head_tail / R5）。"""

from mcp_compress_proxy.compressor.engine import compress
from mcp_compress_proxy.profiles.loader import Profile, RetainRule, TaskProfile, load_profile
from mcp_compress_proxy.store.sqlite_store import SqliteStore
from mcp_compress_proxy.task_context.detector import TaskType


def test_load_profile_roundtrip(tmp_path):
    (tmp_path / "git.yaml").write_text(
        """
server: git
always_preserve_error_signals: false
task_profiles:
  debug:
    retain:
      - path: "commits[0:3].message"
        priority: critical
      - path: "commits[0:3].hash"
    drop:
      - "commits[*].tree"
  fallback:
    retain:
      - "commits[0:3]"
ref_strategy: store_full_payload
""".strip(),
        encoding="utf-8",
    )
    profile = load_profile("git", profiles_dir=tmp_path)
    assert profile.server == "git"
    debug = profile.for_task("debug")
    assert debug.retain[0].path == "commits[0:3].message"
    assert debug.retain[0].priority == "critical"
    assert debug.drop == ["commits[*].tree"]
    assert profile.for_task("fallback") is not None
    assert profile.for_task("explore") is None


def test_error_anchor_keeps_error_lines_with_context(tmp_path):
    lines = [f"line {i}" for i in range(30)]
    lines[12] = "ERROR: boom"
    payload = {"path": "app.py", "content": "\n".join(lines)}
    profile = Profile(
        server="filesystem",
        task_profiles={
            "debug": TaskProfile(
                retain=[RetainRule("path")],
                text_rules=[{"field": "content", "mode": "error_anchor", "keywords": ["error"], "context": 3}],
            )
        },
    )
    result = compress(payload, profile, TaskType.DEBUG, store=SqliteStore(tmp_path / "refs.db"))
    content = result.compressed_payload["content"]
    assert "ERROR: boom" in content
    assert "line 9" in content and "line 15" in content  # 前后 3 行
    assert "line 3" not in content and "line 20" not in content
    assert "sw-compressed" in content  # 缺口有省略标记


def test_head_tail_and_r5_always_preserve(tmp_path):
    lines = [f"line {i}" for i in range(60)]
    lines[40] = "FAILED tests/test_engine.py::test_x"
    payload = {"command": "pytest", "stdout": "\n".join(lines), "exit_code": 1}
    profile = Profile(
        server="terminal",
        always_preserve_error_signals=True,
        task_profiles={
            "explore": TaskProfile(
                retain=[RetainRule("exit_code")],
                drop=["stdout"],
                text_rules=[{"field": "stdout", "mode": "head_tail", "head": 2, "tail": 2}],
            )
        },
    )
    result = compress(payload, profile, TaskType.EXPLORE, store=SqliteStore(tmp_path / "refs.db"))
    stdout = result.compressed_payload["stdout"]
    # head/tail 生效
    assert "line 0" in stdout and "line 59" in stdout
    assert "line 10" not in stdout
    # R5：FAILED 信号行 + 后 5 行被强制保留
    assert "FAILED tests/test_engine.py::test_x" in stdout
    assert "line 45" in stdout
