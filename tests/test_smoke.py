"""阶段一 smoke 测试：仅验证项目骨架与规则文件完整性，不含任何压缩逻辑。"""

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# AGENTS.md 中 7 条架构约束各自必须出现的关键片段
CONSTRAINT_KEYWORDS = [
    "YAML Profile",
    "profiles/{server_name}.yaml",
    "sw-ref:",
    "retrieve_original",
    "Task Context Detector",
    "stdio",
    "transform_manifest",
]


def test_package_imports_with_version():
    import mcp_compress_proxy

    assert mcp_compress_proxy.__version__ == "0.1.0"


def test_skeleton_subpackages_exist():
    expected = ["proxy", "profiles", "compressor", "task_context", "store", "tools"]
    for name in expected:
        init = PROJECT_ROOT / "src" / "mcp_compress_proxy" / name / "__init__.py"
        assert init.is_file(), f"missing subpackage: {name}"


def test_server_profile_files_exist():
    for name in ("git", "filesystem", "terminal"):
        profile = PROJECT_ROOT / "profiles" / f"{name}.yaml"
        assert profile.is_file(), f"missing profile: {profile}"


def test_agents_md_has_exactly_seven_numbered_constraints():
    text = (PROJECT_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    numbered = re.findall(r"^(\d+)\. ", text, flags=re.M)
    assert numbered == [str(i) for i in range(1, 8)], f"found: {numbered}"


def test_agents_md_covers_all_constraint_keywords_and_rules_digest():
    text = (PROJECT_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    for kw in CONSTRAINT_KEYWORDS:
        assert kw in text, f"AGENTS.md missing keyword: {kw}"
    assert "Rules 摘要" in text, "AGENTS.md missing long-session rules digest"
