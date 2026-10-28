"""DeepSeek Harness (DSH) 接入公共逻辑：构造 dsh-mcp-client 插件行。

DSH 没有独立的 MCP 配置段：一个 MCP server 就是 profile 配置里的一条 cordis
插件行（见 dsh-base 的 cordis.patch.yml）：

    - id: mcp-compress-git
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: git
        transport: stdio
        command: <python>
        args: [...]

本模块把「mcp-compress-proxy 包住真实 MCP server」的进程参数、行结构、YAML
方言校验集中在一处，供 scripts/install_dsh_plugin.py、
scripts/verify_dsh_chain.py 与 tests 共用；不 import 任何 DSH 运行时。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]

# 受管块标记：installer 只替换这两个标记之间的内容，绝不碰用户已有配置
MARKER_BEGIN = "# >>> mcp-compress-proxy (managed by scripts/install_dsh_plugin.py)"
MARKER_END = "# <<< mcp-compress-proxy"

# DSH 约束：serverName 必须是 [A-Za-z0-9_-]{1,32}，且同一 scope 内唯一
SERVER_NAME_PATTERN = r"^[A-Za-z0-9_-]{1,32}$"

# 当前接入器认识的真实 MCP server（新增 server = 在这里加一条）
KNOWN_SERVERS = ("git", "filesystem", "fetch", "memory", "sqlite", "github")


class DshConfigError(RuntimeError):
    """接入配置错误（给用户可执行的修复提示）。"""


@dataclass
class ServerSpec:
    """真实 MCP server 的启动方式（代理会把它作为子进程拉起）。"""

    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------- 默认路径


def default_profile_dir() -> Path:
    """DSH profile 目录：优先 DSH_PROFILE_DIR，其次 ~/.dsh/profiles/desktop。"""
    env = os.environ.get("DSH_PROFILE_DIR")
    if env:
        return Path(env)
    return Path.home() / ".dsh" / "profiles" / "desktop"


def profile_patch_path(profile_dir: Path | str | None = None) -> Path:
    return Path(profile_dir or default_profile_dir()) / "cordis.patch.yml"


def default_python(repo_root: Path | None = None) -> Path:
    """优先仓库内 venv 的 python；否则用当前解释器。"""
    root = Path(repo_root or REPO_ROOT)
    for candidate in (
        root / ".venv" / "Scripts" / "python.exe",  # Windows
        root / ".venv" / "bin" / "python",          # POSIX
    ):
        if candidate.exists():
            return candidate
    return Path(sys.executable)


def default_db_path() -> Path:
    """sw-ref 可逆存储位置：MCP_COMPRESS_DB > LOCALAPPDATA/dsh-mcp-compress。"""
    env = os.environ.get("MCP_COMPRESS_DB")
    if env:
        return Path(env)
    base = (
        os.environ.get("LOCALAPPDATA")
        or os.environ.get("XDG_DATA_HOME")
        or os.environ.get("USERPROFILE")
        or Path.home()
    )
    return Path(base) / "dsh-mcp-compress" / "refs.db"


# ---------------------------------------------------------------- server 解析


def _which(name: str, hint: str) -> str:
    found = shutil.which(name)
    if not found:
        raise DshConfigError(hint)
    return found


def resolve_server(
    name: str,
    *,
    repo_root: Path | None = None,
    git_repo: Path | str | None = None,
    fs_roots: Iterable[Path | str] = (),
    sqlite_db: Path | str | None = None,
) -> ServerSpec:
    """把 server 名解析成真实启动命令；缺依赖时抛 DshConfigError。"""
    root = Path(repo_root or REPO_ROOT)
    if name not in KNOWN_SERVERS:
        raise DshConfigError(
            f"unknown server {name!r}; known: {', '.join(KNOWN_SERVERS)}"
        )

    if name == "git":
        command = shutil.which("mcp-server-git")
        if not command:
            raise DshConfigError(
                "mcp-server-git not found on PATH. Install it with:\n"
                "  uv tool install \"mcp-server-git==2026.10.19\" "
                "(mainland: --index-url https://pypi.tuna.tsinghua.edu.cn/simple)"
            )
        repo = Path(git_repo or os.getcwd()).resolve()
        if not repo.exists():
            raise DshConfigError(f"git repository does not exist: {repo}")
        return ServerSpec("git", command, ["--repository", str(repo)])

    if name == "filesystem":
        command = _which(
            "npx",
            "npx not found on PATH; install Node.js >= 18 to use the filesystem server",
        )
        roots = [str(Path(p).resolve()) for p in fs_roots] or [str(root)]
        return ServerSpec(
            "filesystem",
            command,
            ["-y", "@modelcontextprotocol/server-filesystem", *roots],
        )

    if name == "fetch":
        command = _which(
            "uvx",
            "uvx not found on PATH; install uv (https://docs.astral.sh/uv/) for mcp-server-fetch",
        )
        return ServerSpec("fetch", command, ["mcp-server-fetch"])

    if name == "memory":
        command = _which(
            "npx",
            "npx not found on PATH; install Node.js >= 18 for the memory server",
        )
        # memory server 默认把 memory.json 写进当前目录；显式落到用户数据目录，
        # 避免 DSH 进程 cwd 下出现散落的知识图谱文件。
        memory_file = Path(
            os.environ.get("MCP_MEMORY_FILE") or (default_db_path().parent / "memory.json")
        )
        return ServerSpec(
            "memory",
            command,
            ["-y", "@modelcontextprotocol/server-memory"],
            {"MEMORY_FILE_PATH": str(memory_file)},
        )

    if name == "github":
        command = os.environ.get("MCP_GITHUB_SERVER_EXE") or shutil.which(
            "github-mcp-server"
        )
        if not command:
            local = REPO_ROOT / ".e2e" / "tools" / "github" / "github-mcp-server.exe"
            command = str(local) if local.exists() else None
        if not command:
            raise DshConfigError(
                "github-mcp-server not found. Download the official binary:\n"
                "  gh release download v2.0.3 -R github/github-mcp-server "
                "-p github-mcp-server_Windows_x86_64.zip -D .e2e/tools/github\n"
                "  then extract it, or set MCP_GITHUB_SERVER_EXE to the executable."
            )
        return ServerSpec("github", command, ["stdio"])

    # sqlite
    command = _which(
        "uvx",
        "uvx not found on PATH; install uv (https://docs.astral.sh/uv/) for mcp-server-sqlite",
    )
    db = Path(sqlite_db or (default_db_path().parent / "sqlite.db")).resolve()
    # mcp-server-sqlite 0.1.0 仍调用 mcp<2 的旧 API（Server.list_resources），
    # 必须把 uvx 环境里的 mcp 钉在 1.x，否则启动即 AttributeError。
    return ServerSpec(
        "sqlite",
        command,
        ["--with", "mcp<2", "mcp-server-sqlite", "--db-path", str(db)],
    )


# ---------------------------------------------------------------- 行构造


def build_row(
    spec: ServerSpec,
    *,
    python: Path | str | None = None,
    repo_root: Path | None = None,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """构造一条 @deepseek-ai/dsh-mcp-client 插件行。

    进程链：DSH → python scripts/dsh_entry.py → mcp_compress_proxy.proxy
            → 真实 server（spec.command + spec.args）
    """
    root = Path(repo_root or REPO_ROOT)
    entry = root / "scripts" / "dsh_entry.py"
    profiles = root / "profiles"
    db = Path(db_path or default_db_path())
    args = [
        str(entry),
        "--server",
        spec.name,
        "--db",
        str(db),
        "--profiles-dir",
        str(profiles),
        "--",
        spec.command,
        *spec.args,
    ]
    config: dict[str, Any] = {
        "serverName": spec.name,
        "transport": "stdio",
        "command": str(python or default_python(root)),
        "args": args,
        # git log / 大文件读取可能超过默认 60s
        "toolCallTimeoutMs": 120000,
        # 单个 server 起不来不应该拖垮整个 harness
        "failOnStartupError": False,
    }
    if spec.env:
        config["env"] = dict(spec.env)
    return {
        "id": f"mcp-compress-{spec.name}",
        "name": "@deepseek-ai/dsh-mcp-client",
        "config": config,
    }


def build_rows(
    servers: Iterable[str],
    *,
    python: Path | str | None = None,
    repo_root: Path | None = None,
    db_path: Path | str | None = None,
    git_repo: Path | str | None = None,
    fs_roots: Iterable[Path | str] = (),
    sqlite_db: Path | str | None = None,
) -> list[dict[str, Any]]:
    root = Path(repo_root or REPO_ROOT)
    py = Path(python or default_python(root))
    rows: list[dict[str, Any]] = []
    for name in servers:
        spec = resolve_server(
            name,
            repo_root=root,
            git_repo=git_repo,
            fs_roots=fs_roots,
            sqlite_db=sqlite_db,
        )
        rows.append(build_row(spec, python=py, repo_root=root, db_path=db_path))
    return rows


# ---------------------------------------------------------------- YAML 方言


class DshPatchLoader(yaml.SafeLoader):
    """DSH 的 entry-list 方言：!!js 标量（表达式）原样保留为占位值。"""


def _construct_js(loader: yaml.SafeLoader, node: yaml.Node) -> dict[str, str]:
    return {"__js__": loader.construct_scalar(node)}  # type: ignore[arg-type]


DshPatchLoader.add_constructor("tag:yaml.org,2002:js", _construct_js)


def parse_patch(text: str) -> list[Any]:
    """解析 cordis.patch.yml；容忍 !!js 表达式，其余语法错误照常抛出。"""
    data = yaml.load(text, Loader=DshPatchLoader)
    if data is None:
        return []
    if not isinstance(data, list):
        raise DshConfigError("cordis.patch.yml 顶层必须是 YAML 列表")
    return data


def validate_patch(text: str) -> list[Any]:
    """校验（不落盘）并返回解析结果。"""
    return parse_patch(text)


class _QuotedDumper(yaml.SafeDumper):
    """对 YAML plain scalar 不安全的值加单引号（Windows 路径的 \\ / # / : 等）。"""


_PLAIN_UNSAFE = set("\\#{}[]&*!|>'\"%@\u0060")


def _needs_quotes(data: str) -> bool:
    if not data or data != data.strip():
        return True
    if any(ch in _PLAIN_UNSAFE for ch in data):
        return True
    if ": " in data or data.startswith(("- ", "? ", ": ")):
        return True
    return False


def _represent_str(dumper: yaml.SafeDumper, data: str) -> yaml.Node:
    style = "'" if _needs_quotes(data) else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_QuotedDumper.add_representer(str, _represent_str)


def render_block(rows: list[dict[str, Any]]) -> str:
    """把一个 insert 列表渲染成受管块（含起止标记）。"""
    body = yaml.dump(
        [{"insert": rows}],
        Dumper=_QuotedDumper,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=120,
    )
    return f"{MARKER_BEGIN}\n{body.rstrip()}\n{MARKER_END}\n"


def apply_block(text: str, block: str) -> str:
    """用新受管块替换旧受管块；没有则追加到末尾（保留用户其余内容）。"""
    begin = text.find(MARKER_BEGIN)
    if begin != -1:
        end = text.find(MARKER_END, begin)
        if end == -1:
            raise DshConfigError(
                f"发现 {MARKER_BEGIN!r} 但缺少结束标记 {MARKER_END!r}，请先手工修复"
            )
        end += len(MARKER_END)
        while end < len(text) and text[end] == "\n":
            end += 1
        return text[:begin] + block + text[end:]
    prefix = text.rstrip() + "\n\n" if text.strip() else ""
    return prefix + block


def remove_block(text: str) -> str:
    """删除受管块；没有则原样返回。"""
    begin = text.find(MARKER_BEGIN)
    if begin == -1:
        return text
    end = text.find(MARKER_END, begin)
    if end == -1:
        raise DshConfigError(
            f"发现 {MARKER_BEGIN!r} 但缺少结束标记 {MARKER_END!r}，请先手工修复"
        )
    end += len(MARKER_END)
    while end < len(text) and text[end] == "\n":
        end += 1
    remaining = text[:begin] + text[end:]
    return remaining.rstrip() + "\n" if remaining.strip() else ""


def managed_rows(data: list[Any]) -> list[dict[str, Any]]:
    """取出受管行（id 以 mcp-compress- 开头）。"""
    rows: list[dict[str, Any]] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        for row in entry.get("insert") or []:
            if isinstance(row, dict) and str(row.get("id", "")).startswith(
                "mcp-compress-"
            ):
                rows.append(row)
    return rows


# ---------------------------------------------------------------- 预检


def check_python_import(python: Path | str) -> tuple[bool, str]:
    """确认目标解释器能 import mcp_compress_proxy（代理进程的前置条件）。"""
    try:
        proc = subprocess.run(
            [str(python), "-c", "import mcp_compress_proxy; print(mcp_compress_proxy.__file__)"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    detail = (proc.stdout or proc.stderr or "").strip()
    return proc.returncode == 0, detail


def backup_path(path: Path) -> Path:
    """唯一备份名：同一秒内重复安装不会覆盖上一份原始备份。"""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    candidate = path.with_name(f"{path.name}.bak-{stamp}")
    counter = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.name}.bak-{stamp}-{counter}")
        counter += 1
    return candidate


def describe_row(row: dict[str, Any]) -> str:
    config = row.get("config") or {}
    args = config.get("args") or []
    return (
        f"{row.get('id')}: serverName={config.get('serverName')} "
        f"command={config.get('command')} args={len(args)}"
    )