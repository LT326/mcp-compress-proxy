# dsh-plugin-mcp-compress-proxy

DeepSeek Harness（DSH）bundle：把 MCP server 包在
[mcp-compress-proxy](https://github.com/LT326/mcp-compress-proxy) 后面，让 DSH 拿到的
MCP 工具输出先经过任务感知压缩；被丢弃的字段落 SQLite，可用注入的
`retrieve_original` 无损取回。

## 它做了什么

DSH 没有独立的 MCP 配置段——一个 MCP server 就是 profile 配置里的一条 cordis 插件行：

```yaml
- id: mcp-compress-git
  name: '@deepseek-ai/dsh-mcp-client'
  config:
    serverName: git
    transport: stdio
    command: <python>
    args: [-m, mcp_compress_proxy.proxy, --server, git, ..., --, mcp-server-git, ...]
```

本 bundle 的 `cordis.patch.yml` 插入 5 行（git / filesystem / memory / sqlite /
github），**每个 server 只有在设置了对应环境变量时才启用**。启用后 DSH 里会多出
`mcp__<server>__*` 工具，并额外注入 `mcp__<server>__retrieve_original`。

## 前置条件

1. Python 包（尚未上 PyPI 时用 git 安装）：

   ```powershell
   pip install mcp-compress-proxy
   # 或：
   pip install "git+https://github.com/LT326/mcp-compress-proxy"
   ```

2. 对应的真实 MCP server：git 用 `mcp-server-git`，filesystem/memory 用 `npx`，
   sqlite 用 `uvx`，github 用官方 `github-mcp-server` 二进制。

## 安装

```powershell
dsh plugin --profile desktop add dsh-plugin-mcp-compress-proxy
```

（本地开发也可以直接 `file:<repo>\integrations\dsh`；先确保 `dsh.profile.bundles`
里有这个包名，或在 Web 的 Plugins 页勾选。）

## 启用某个 server

| 变量 | 启用 | 说明 |
|---|---|---|
| `MCP_COMPRESS_PYTHON` | — | 运行代理的 python（默认 `python`/`python3`） |
| `MCP_COMPRESS_DB` | — | sw-ref SQLite 路径（默认 `%LOCALAPPDATA%\dsh-mcp-compress\refs.db`） |
| `MCP_GIT_REPOSITORY` | git | 被代理的仓库 |
| `MCP_GIT_SERVER` | — | 真实 git server（默认 `mcp-server-git`） |
| `MCP_FS_ROOT` | filesystem | 允许访问的根目录 |
| `MCP_MEMORY_FILE` | memory | 知识图谱 JSON 路径 |
| `MCP_SQLITE_DB` | sqlite | 数据库文件 |
| `MCP_GITHUB_SERVER_EXE` | github | github-mcp-server 可执行文件 |
| `GITHUB_PERSONAL_ACCESS_TOKEN` | — | github token（没设时 `dsh_entry.py` 会尝试 `gh auth token`） |

例：

```powershell
$env:MCP_GIT_REPOSITORY = "E:\myrepo"
$env:MCP_COMPRESS_PYTHON = "C:\Python312\python.exe"
# 重启 DSH
```

## 本机安装器（推荐给仓库用户）

仓库里的 `scripts/install_dsh_plugin.py` 会烘焙绝对路径、可多选 server、幂等可撤销，
比手写环境变量更省事。完整说明见
[docs/dsh-integration.md](https://github.com/LT326/mcp-compress-proxy/blob/main/docs/dsh-integration.md)。

> 静态 patch 只支持“一个 server 一个固定 env”；需要同一 server 多实例（如两个仓库）
> 时，用安装器或自己复制行并改 `serverName`。