"""真实 mcp-server-git E2E 的 pytest 包装。

默认跳过（不依赖网络/uvx）；设置 MCP_E2E=1 开启：
    set MCP_E2E=1 && .venv\\Scripts\\python.exe -m pytest tests/test_e2e_git.py -v
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("MCP_E2E") != "1",
    reason="real-server E2E (needs uvx + git + network); set MCP_E2E=1 to enable",
)

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "e2e_mcp_git.py"


def test_real_mcp_git_server_e2e():
    venv_py = Path(__file__).resolve().parents[1] / ".venv" / "Scripts" / "python.exe"
    py = str(venv_py) if venv_py.exists() else sys.executable
    out = subprocess.run(
        [py, str(SCRIPT), "--keep"],
        capture_output=True,
        timeout=900,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    tail = "\n".join((out.stdout or "").splitlines()[-40:])
    assert out.returncode == 0, f"E2E script failed (rc={out.returncode}):\n{tail}\n{out.stderr[-2000:]}"
