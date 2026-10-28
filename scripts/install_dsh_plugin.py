r"""把 mcp-compress-proxy 安装成 DeepSeek Harness 的 MCP 插件行（幂等）。

DSH 的 MCP 接入 = profile 的 cordis.patch.yml 里插入 @deepseek-ai/dsh-mcp-client
行。本脚本在 profile patch 末尾维护一个「受管块」，只改块内内容：

    # >>> mcp-compress-proxy (managed by scripts/install_dsh_plugin.py)
    - insert:
        - id: mcp-compress-git
          name: '@deepseek-ai/dsh-mcp-client'
          config: { ... }
    # <<< mcp-compress-proxy

用法：
    python scripts/install_dsh_plugin.py --servers git --git-repo E:\myrepo
    python scripts/install_dsh_plugin.py --servers git,fetch --dry-run
    python scripts/install_dsh_plugin.py --remove

注意：DSH 默认开启配置热重载（HMR），写入 profile patch 会触发重新组合；
请在会话之间执行，或写入后重启 DSH。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import dsh_common as dc  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="install_dsh_plugin.py",
        description="Install mcp-compress-proxy as DeepSeek Harness MCP plugin rows",
        allow_abbrev=False,
    )
    parser.add_argument(
        "--servers",
        default="git",
        help=f"comma-separated servers to expose (known: {','.join(dc.KNOWN_SERVERS)})",
    )
    parser.add_argument("--git-repo", default=None, help="git server 的仓库路径（默认当前目录）")
    parser.add_argument(
        "--fs-roots", action="append", default=[], help="filesystem server 允许的根目录（可重复）"
    )
    parser.add_argument("--sqlite-db", default=None, help="sqlite server 的数据库文件")
    parser.add_argument("--python", default=None, help="运行代理的 Python（默认仓库 .venv）")
    parser.add_argument("--db", default=None, help="sw-ref SQLite 存储路径")
    parser.add_argument(
        "--profile-dir",
        default=None,
        help="DSH profile 目录（默认 $DSH_PROFILE_DIR 或 ~/.dsh/profiles/desktop）",
    )
    parser.add_argument("--dry-run", action="store_true", help="只打印，不写文件")
    parser.add_argument("--remove", action="store_true", help="删除受管块")
    parser.add_argument("--force", action="store_true", help="跳过 Python 导入预检")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    profile_dir = Path(args.profile_dir) if args.profile_dir else dc.default_profile_dir()
    patch = dc.profile_patch_path(profile_dir)

    if not patch.exists() and args.remove:
        print(f"[dsh] no patch file at {patch}; nothing to remove")
        return 0

    try:
        original = patch.read_text(encoding="utf-8") if patch.exists() else ""
        dc.validate_patch(original)

        if args.remove:
            updated = dc.remove_block(original)
            dc.validate_patch(updated)
            if not args.dry_run:
                patch.parent.mkdir(parents=True, exist_ok=True)
                if patch.exists():
                    backup = dc.backup_path(patch)
                    backup.write_text(original, encoding="utf-8")
                    print(f"[dsh] backup: {backup}")
                patch.write_text(updated, encoding="utf-8")
            print(f"[dsh] removed mcp-compress block from {patch}")
            return 0

        servers = [s.strip() for s in args.servers.split(",") if s.strip()]
        python = Path(args.python) if args.python else dc.default_python()
        db = Path(args.db) if args.db else dc.default_db_path()

        ok, detail = dc.check_python_import(python)
        if not ok and not args.force:
            print(f"[dsh] python preflight failed: {python}", file=sys.stderr)
            print(f"[dsh]   {detail}", file=sys.stderr)
            print(
                "[dsh] install the package into that interpreter:\n"
                "       <repo>\\.venv\\Scripts\\python.exe -m pip install -e .\n"
                "     or pass --python <interpreter> / --force to skip this check",
                file=sys.stderr,
            )
            return 2
        if not ok:
            print(f"[dsh] warning: python preflight failed, continuing (--force): {detail}")

        profiles_dir = dc.REPO_ROOT / "profiles"
        for name in servers:
            if not (profiles_dir / f"{name}.yaml").exists():
                print(
                    f"[dsh] note: no profiles/{name}.yaml yet - the proxy will pass "
                    "MCP output through unchanged (retrieve_original is still injected)"
                )

        rows = dc.build_rows(
            servers,
            python=python,
            db_path=db,
            git_repo=Path(args.git_repo) if args.git_repo else None,
            fs_roots=args.fs_roots,
            sqlite_db=args.sqlite_db,
        )
        block = dc.render_block(rows)
        updated = dc.apply_block(original, block)
        dc.validate_patch(updated)

        db.parent.mkdir(parents=True, exist_ok=True)
        # memory server 的 MEMORY_FILE_PATH 目录也建好（server 只写文件不建目录）
        for row in rows:
            memory_file = (row["config"].get("env") or {}).get("MEMORY_FILE_PATH")
            if memory_file and not args.dry_run:
                Path(memory_file).parent.mkdir(parents=True, exist_ok=True)
        if args.dry_run:
            print(f"[dsh] dry-run: would write {len(rows)} row(s) to {patch}\n")
            print(block)
            return 0

        patch.parent.mkdir(parents=True, exist_ok=True)
        if patch.exists():
            backup = dc.backup_path(patch)
            backup.write_text(original, encoding="utf-8")
            print(f"[dsh] backup: {backup}")
        patch.write_text(updated, encoding="utf-8")

        print(f"[dsh] wrote {len(rows)} plugin row(s) to {patch}")
        for row in rows:
            print(f"[dsh]   {dc.describe_row(row)}")
        print(f"[dsh] refs db: {db}")
        print("[dsh] next: restart DeepSeek Harness (or let HMR reload the profile),")
        print("[dsh]       then look for tools named mcp__<server>__* in a session.")
        return 0
    except dc.DshConfigError as exc:
        print(f"[dsh] error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())