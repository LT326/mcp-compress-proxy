# mcp-compress-proxy

任务感知的 MCP 透明压缩代理（Task-Aware MCP Compression Proxy）。

面向 ZCode / Claude Code / Cursor 等 MCP 客户端：代理 spawn 原始 MCP server
（第一版仅 git / filesystem / terminal），按当前任务类型（debug / review / explore）
用 YAML Profile 驱动字段级压缩；被丢弃的数据落入 SQLite，可随时通过注入的
`retrieve_original` 工具无损取回。

- 架构约束与阶段状态：见 [AGENTS.md](AGENTS.md)
- 当前进度：四个阶段全部完成（2026-10-24）；44 个测试全绿，benchmark 见
  [tests/benchmark/report.md](tests/benchmark/report.md)（平均压缩率 81.5%、关键字段保留率 100%、retrieve 率 3.6%）
- 真实会话基准（2026-10-25）：[tests/benchmark/real_sessions_report.md](tests/benchmark/real_sessions_report.md)
  （10 任务 × 2 臂真实 LLM 会话：成功率无损，总 token +22.8%，真实 retrieve 率 60%——价值边界与适用场景见报告结论）
- P0 优化 + 复测（2026-10-25）：[tests/benchmark/real_sessions_report_v2.md](tests/benchmark/real_sessions_report_v2.md)
  （意图感知透传 + 部分取回 + commit_digest 块级摘要：retrieve 6→0，深扫任务 token 改善 31-34%，
  git_log 输出省 34%，剩余总 token 差距为单样本行为方差）

## 接入 ZCode

> ⚠️ 2026-10 核验事实：npm 包 `@modelcontextprotocol/server-git` 已下架（registry 返回
> E404，官方参考 server 已归档至 servers-archived）。当前官方 git MCP server 是
> **PyPI 包 `mcp-server-git`**，用 `uvx mcp-server-git --repository <repo>` 启动。
> 详见 [docs/e2e-verification.md](docs/e2e-verification.md)。

本仓库已写好配置（用户级 `C:\Users\a\.zcode\cli\config.json` 与工作区级
`E:\GLM\.zcode\config.json` 内容相同，同名时用户级优先）：

```json
{
  "mcp": {
    "servers": {
      "git-compressed": {
        "command": "E:\\GLM\\mcp-compress-proxy\\.venv\\Scripts\\python.exe",
        "args": [
          "E:\\GLM\\mcp-compress-proxy\\scripts\\zcode_entry.py",
          "--server", "git",
          "--db", "E:\\GLM\\mcp-compress-proxy\\.e2e\\zcode_refs.db",
          "--",
          "C:\\Users\\a\\.local\\bin\\mcp-server-git.exe",
          "--repository", "E:\\GLM\\mcp-compress-proxy\\.e2e\\demo-repo"
        ]
      }
    }
  }
}
```

要点（为什么不是直接 `-m mcp_compress_proxy.proxy` + uvx）：
- **ZCode GUI 拉起子进程的环境极简**（日志实测 `spawn powershell.exe ENOENT`），
  PATH 里没有 git → server 内的 GitPython 在 import 时报 "Bad git executable"
  直接崩溃 → 表现为 MCP 连接 30 秒超时循环。
- `scripts/zcode_entry.py` 启动入口先从注册表补全 PATH、设置
  `GIT_PYTHON_GIT_EXECUTABLE` 绝对路径、补 TEMP 等基础变量，再进入代理主循环。
- server 用 `uv tool install` 固化为本机 exe（拉起零网络、零 uvx 解析）：
  `uv tool install --python E:\python-312\python.exe mcp-server-git==2026.10.19`
- `--repository` 换成你自己的仓库路径即可代理任意仓库。
- 原始 payload 存入 SQLite（`--db`），被压缩内容用注入的 `retrieve_original` 取回。
- 长输出（如 `git_log`）会看到 `[sw-compressed; original: sw-ref:… — call retrieve_original]`
  标记行；短输出（如 `git_status`）原样透传。
- 空环境冒烟验证：`python scripts\verify_zcode_chain.py`（模拟 ZCode 残缺环境）。

## 真实 E2E（已跑通）

```bash
# 直连 + 经代理双链路（真实 mcp-server-git，临时仓库自动建、自动清理）
.venv\Scripts\python.exe scripts\e2e_mcp_git.py

# pytest 门控形式（默认跳过，不依赖网络）
set MCP_E2E=1 && .venv\Scripts\python.exe -m pytest tests\test_e2e_git.py -v
```

完整证据（版本矩阵、命令、输出、常见问题、清理）见
[docs/e2e-verification.md](docs/e2e-verification.md)。

## 开发环境

- Python：`E:\python-312`（3.12.x）
- 装包一律用 uv（如 `uv venv` + `uv pip install -e ".[dev]"`），不要直接 pip
- 运行测试：`python -m pytest tests/ -v`
