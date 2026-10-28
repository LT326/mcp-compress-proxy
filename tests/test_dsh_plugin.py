
"""DSH 接入的离线测试：行结构、YAML 方言、受管块幂等、bundle manifest。"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import dsh_common as dc  # noqa: E402


def _row(tmp_path: Path) -> dict:
    spec = dc.ServerSpec("git", "mcp-server-git", ["--repository", str(tmp_path)])
    return dc.build_row(
        spec, python="python.exe", repo_root=ROOT, db_path=tmp_path / "refs.db"
    )


def test_build_row_shape(tmp_path: Path) -> None:
    row = _row(tmp_path)
    assert row["id"] == "mcp-compress-git"
    assert row["name"] == "@deepseek-ai/dsh-mcp-client"

    config = row["config"]
    assert re.match(dc.SERVER_NAME_PATTERN, config["serverName"])
    assert config["transport"] == "stdio"
    assert config["command"] == "python.exe"
    assert config["failOnStartupError"] is False
    assert config["toolCallTimeoutMs"] >= 60_000

    args = config["args"]
    assert args[0].endswith("dsh_entry.py")
    assert Path(args[0]).exists(), "入口脚本必须真实存在"
    assert args[1:3] == ["--server", "git"]
    assert "--profiles-dir" in args
    assert str(tmp_path / "refs.db") in args
    separator = args.index("--")
    assert args[separator + 1] == "mcp-server-git"
    assert args[separator + 2 :] == ["--repository", str(tmp_path)]


def test_build_row_carries_server_env(tmp_path: Path) -> None:
    spec = dc.ServerSpec(
        "memory", "npx", ["-y", "@modelcontextprotocol/server-memory"],
        {"MEMORY_FILE_PATH": str(tmp_path / "memory.json")},
    )
    row = dc.build_row(spec, python="python.exe", repo_root=ROOT, db_path=tmp_path / "refs.db")
    assert row["config"]["env"]["MEMORY_FILE_PATH"].endswith("memory.json")
    assert row["config"]["serverName"] == "memory"


def test_render_block_roundtrip(tmp_path: Path) -> None:
    text = dc.apply_block("", dc.render_block([_row(tmp_path)]))
    data = dc.parse_patch(text)
    rows = dc.managed_rows(data)
    assert len(rows) == 1
    assert rows[0]["config"]["serverName"] == "git"


def test_apply_block_preserves_user_content_and_is_idempotent(tmp_path: Path) -> None:
    existing = (
        "# 用户自己的 patch\n"
        "- id: ui-chat\n"
        "  name: '@deepseek-ai/dsh-client-ui-chat'\n"
        "  config:\n"
        "    transcriptView: standard\n"
    )
    block = dc.render_block([_row(tmp_path)])
    once = dc.apply_block(existing, block)
    twice = dc.apply_block(once, block)

    assert twice.count(dc.MARKER_BEGIN) == 1
    assert twice.count(dc.MARKER_END) == 1
    assert "- id: ui-chat" in twice
    assert dc.parse_patch(twice)


def test_patch_dialect_tolerates_js_expressions() -> None:
    text = "- id: x\n  disabled: !!js '!process.env.FOO'\n"
    data = dc.parse_patch(text)
    assert data[0]["disabled"] == {"__js__": "!process.env.FOO"}


def test_remove_block_restores_original(tmp_path: Path) -> None:
    existing = "- id: timer\n  name: '@deepseek-ai/cordis-plugin-timer'\n"
    once = dc.apply_block(existing, dc.render_block([_row(tmp_path)]))
    restored = dc.remove_block(once)
    assert restored.strip() == existing.strip()


def test_bundle_patch_matches_schema() -> None:
    text = (ROOT / "integrations" / "dsh" / "cordis.patch.yml").read_text(encoding="utf-8")
    rows = dc.managed_rows(dc.parse_patch(text))
    assert rows, "bundle patch 必须包含至少一条受管行"
    for row in rows:
        assert row["name"] == "@deepseek-ai/dsh-mcp-client"
        assert re.match(dc.SERVER_NAME_PATTERN, row["config"]["serverName"])
        assert row["config"]["transport"] == "stdio"
        expressions = [
            value["__js__"]
            for value in _walk(row)
            if isinstance(value, dict) and "__js__" in value
        ]
        assert expressions, "静态 bundle 应该用 !!js 读取环境变量"
        assert all("process.env" in expr for expr in expressions)


def _walk(node):
    if isinstance(node, dict):
        for value in node.values():
            yield value
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield value
            yield from _walk(value)


def test_bundle_manifest_declares_patch() -> None:
    import json

    pkg = json.loads(
        (ROOT / "integrations" / "dsh" / "package.json").read_text(encoding="utf-8")
    )
    assert pkg["dsh"]["bundle"]["patch"] == "./cordis.patch.yml"
    assert pkg["main"] == "index.js"
    assert "cordis.patch.yml" in pkg["files"]


def test_resolve_server_rejects_unknown() -> None:
    with pytest.raises(dc.DshConfigError):
        dc.resolve_server("postgres", repo_root=ROOT)


def test_installer_dry_run_does_not_write(tmp_path: Path, monkeypatch, capsys) -> None:
    import install_dsh_plugin as installer

    monkeypatch.setattr(
        dc,
        "resolve_server",
        lambda name, **kwargs: dc.ServerSpec(name, "fake-server", ["--repo", "x"]),
    )
    monkeypatch.setattr(dc, "check_python_import", lambda python: (True, "ok"))

    patch = tmp_path / "cordis.patch.yml"
    code = installer.main(
        [
            "--servers",
            "git",
            "--profile-dir",
            str(tmp_path),
            "--dry-run",
        ]
    )
    assert code == 0
    assert not patch.exists()
    out = capsys.readouterr().out
    assert "mcp-compress-git" in out
    assert "dry-run" in out


def test_installer_writes_and_removes(tmp_path: Path, monkeypatch) -> None:
    import install_dsh_plugin as installer

    monkeypatch.setattr(
        dc,
        "resolve_server",
        lambda name, **kwargs: dc.ServerSpec(name, "fake-server", []),
    )
    monkeypatch.setattr(dc, "check_python_import", lambda python: (True, "ok"))

    patch = tmp_path / "cordis.patch.yml"
    patch.write_text("# keep me\n", encoding="utf-8")

    assert installer.main(["--servers", "git", "--profile-dir", str(tmp_path)]) == 0
    written = patch.read_text(encoding="utf-8")
    assert "# keep me" in written
    assert dc.managed_rows(dc.parse_patch(written))

    assert installer.main(["--profile-dir", str(tmp_path), "--remove"]) == 0
    assert dc.managed_rows(dc.parse_patch(patch.read_text(encoding="utf-8"))) == []
    assert patch.read_text(encoding="utf-8").strip() == "# keep me"


def test_installer_reports_bad_python(tmp_path: Path, monkeypatch, capsys) -> None:
    import install_dsh_plugin as installer

    monkeypatch.setattr(
        dc,
        "resolve_server",
        lambda name, **kwargs: dc.ServerSpec(name, "fake-server", []),
    )
    monkeypatch.setattr(dc, "check_python_import", lambda python: (False, "no module"))

    code = installer.main(["--servers", "git", "--profile-dir", str(tmp_path)])
    assert code == 2
    assert "python preflight failed" in capsys.readouterr().err