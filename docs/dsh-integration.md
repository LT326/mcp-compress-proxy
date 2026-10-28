# DeepSeek Harness（DSH）接入

> 目标：让 DSH 通过 `@deepseek-ai/dsh-mcp-client` 使用 MCP server 时，工具输出先经过
> mcp-compress-proxy 的任务感知压缩，并保留 sw-ref 可逆取回。
>
> 本文只描述 DSH 侧接线；压缩规则本身仍只存在于 `profiles/*.yaml`（AGENTS.md R1）。

## 1. 为什么是「插件行」而不是别的东西

DSH 没有 `mcpServers` 配置段。MCP 接入由 `@deepseek-ai/dsh-mcp-client` 完成，
而它自己就是一条 cordis 插件行：每个 server 一条，写在 profile 的
`cordis.patch.yml`（或某个 bundle 的 patch）里：

```yaml
- insert:
    - id: mcp-compress-git
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: git
        transport: stdio
        command: <python>
        args: [...]
```

DSH 会把 `serverName` + 工具名拼成模型可见的 `mcp__<serverName>__<tool>`，
并负责协议协商、重连、工具列表同步。我们只需要提供一条 `stdio` 命令：
**python → 代理 → 真实 MCP server**。

进程链：

```
DSH agent
  └─ @deepseek-ai/dsh-mcp-client (stdio)
       └─ <python> scripts/dsh_entry.py --server git --db ... -- <真实 server> ...
            └─ mcp_compress_proxy.proxy  (透明转发 + 压缩 + retrieve_original)
                 └─ mcp-server-git --repository <repo>
```

`scripts/dsh_entry.py` 复用 `scripts/zcode_entry.py` 的环境修复（注册表 PATH、
`GIT_PYTHON_GIT_EXECUTABLE`、TEMP 兜底），因为 Electron GUI 拉起的子进程环境
可能不完整（R15）。

## 2. 安装

### 2.1 本机安装器（推荐）

```powershell
# 仓库根目录；DSH 的 profile 默认取 $env:DSH_PROFILE_DIR，否则 ~/.dsh/profiles/desktop
.venv\Scripts\python.exe scripts\install_dsh_plugin.py --servers git --git-repo E:\myrepo
```

它会：

1. 预检目标 python 能否 `import mcp_compress_proxy`（否则给出 editable 安装命令）；
2. 解析真实 server（默认 PATH 上的 `mcp-server-git`）；
3. 在 `cordis.patch.yml` 末尾维护一个受管块（起止标记之间），写入绝对路径：
   python / `scripts/dsh_entry.py` / `profiles` 目录 / SQLite / 真实 server；
4. 写前备份 `cordis.patch.yml.bak-<时间戳>`，写后重新解析校验 YAML。

可重复执行（幂等）；`--dry-run` 只打印；`--remove` 删除受管块。
其它 server：`--servers git,filesystem,fetch,memory,sqlite,github`（需要对应
`npx` / `uvx` / GitHub 二进制可用）。注意：目前只有 `git` 有经过真实 E2E 的 Profile，其它
server 会被接线但只做透传（见 §6）。

> ⚠️ DSH 默认开启配置热重载（HMR）：写 `cordis.patch.yml` 会触发 profile 重新
> 组合。请在**会话之间**执行，或写入后重启 DSH；不要在长任务执行中改。

### 2.2 作为 bundle 安装

仓库内 `integrations/dsh/` 是一个合法的 DSH bundle（`package.json` 里
`dsh.bundle.patch` 指向 `cordis.patch.yml`）：

```bash
dsh plugin --profile desktop add file:<PROJECT_DIR>\integrations\dsh
```

> 如果 `dsh` 不在 PATH 上（桌面版默认如此），可以用 Electron 的 Node 模式直接跑 CLI：
> `ELECTRON_RUN_AS_NODE=1 "<DSH_INSTALL>\DeepSeek Harness.exe" "<DSH_INSTALL>\resources\app.asar\dsh\node_modules\@deepseek-ai\dsh\lib\bin.js"`。
> 注意 `dsh plugin` 只是把参数转给 profile 里的 pnpm；装完还要把包名加进
> `dsh.profile.bundles`（或在 Web 的 Plugins 页勾选）。

静态 patch 不写死路径，用环境变量启用：

| 变量 | 必填 | 作用 |
|---|---|---|
| `MCP_GIT_REPOSITORY` | 是（启用 git 行） | 被代理的 git 仓库 |
| `MCP_COMPRESS_PYTHON` | 否 | 能 import 本包的 python（默认 `python`/`python3`） |
| `MCP_COMPRESS_DB` | 否 | sw-ref SQLite 路径 |
| `MCP_COMPRESS_PROFILES_DIR` | 否 | profiles 目录（非 editable 安装时用） |
| `MCP_GIT_SERVER` | 否 | 真实 git server（默认 `mcp-server-git`） |

## 3. 验证（不启动 DSH）

```powershell
.venv\Scripts\python.exe scripts\verify_dsh_chain.py
```

它按 DSH 完全相同的 command/args 拉起代理，用官方 MCP SDK（`mcp` 2.3.0，
支持 2026-07-28 修订版）走一遍：

1. `initialize` 握手（协议协商）；
2. `tools/list` 确认注入的 `retrieve_original`；
3. `tools/call git_log(max_count=60)` 触发压缩，检查 `sw-compressed` / `sw-ref`；
4. `tools/call retrieve_original` 取回原文，检查 `resultType == "complete"`（R16）。

通过输出：

```
PASS: DSH chain (spawn -> MCP handshake -> compression -> reversible retrieve)
```

## 4. 在 DSH 里确认

重启（或等 HMR 重载）后，新会话的工具列表应出现：

- `mcp__git__git_log`、`mcp__git__git_show` …（真实 git server 的工具）
- `mcp__git__retrieve_original`（代理注入）

宽范围查询（`git_log` 且 `max_count > 20`）返回块级摘要，末尾带
`[sw-compressed; original: sw-ref:… — call retrieve_original]`；窄查询
（`git_show` / `git_diff` / `max_count ≤ 20`）原样透传（R18）。

## 5. 排障

| 现象 | 原因 / 处理 |
|---|---|
| 工具列表里没有 `mcp__git__*` | 看 DSH 日志里的 `mcp-client(git)` 报错；先手工跑一遍 verify 脚本 |
| verify 报 `No module named mcp_compress_proxy` | 目标 python 没装本包：`<repo>\.venv\Scripts\python.exe -m pip install -e <repo>` |
| `spawn mcp-server-git ENOENT` | 装 `uv tool install "mcp-server-git==2026.10.19"`，或用 `MCP_GIT_SERVER` 指定绝对路径 |
| 压缩没生效（无 `sw-compressed`） | 调用被 `skip_compression_if` 判定为窄意图；或 server 名没匹配到 `profiles/<name>.yaml`（看 stderr 的 passthrough 日志） |
| `retrieve_original` 不存在 | 客户端在 `tools/list` 之前就缓存了工具列表；重启会话 |
| sqlite 工具没出现，日志有 `AttributeError: 'Server' object has no attribute 'list_resources'` | mcp-server-sqlite 用了 mcp 2.x；安装器已带 `--with "mcp<2"`，确认该参数没被手工改掉 |
| 首次启动 sqlite 很慢 | uvx 首次会下载 mcp 1.x 依赖；可先在终端跑一次 `uvx --with "mcp<2" mcp-server-sqlite --help` 预热缓存 |
| github 工具没出现 / `github-mcp-server not found` | 安装器会找 `.e2e/tools/github/` 或 `MCP_GITHUB_SERVER_EXE`；按 docs 的 `gh release download` 命令下载解压 |
| github 工具调用报 401 | 代理进程没拿到 token：确认本机 `gh auth status` 已登录（`dsh_entry.py` 会调 `gh auth token`），或在 MCP 行里显式配 `env.GITHUB_PERSONAL_ACCESS_TOKEN` |
| 修改 patch 后 DSH 行为没变 | 没有 HMR 时需要重启；确认改的是 `$DSH_PROFILE_DIR/cordis.patch.yml` |

回滚：`.venv\Scripts\python.exe scripts\install_dsh_plugin.py --remove`，或用同目录的
`cordis.patch.yml.bak-*` 覆盖。

## 6. 边界

- **每个 profile 一份 server**：`serverName` 在 scope 内必须唯一；多个 server
  就是多条插件行（安装器 `--servers` 支持多选）。
- **一个 git server 固定一个仓库**：`mcp-server-git` 只接受启动参数
  `--repository`。DSH 的会话 cwd 可以不同，但 MCP server 是 profile 级的；
  需要多仓库时，为每个仓库建一条行、换不同 `serverName`。
- **工具名会变长**：`mcp__git__git_log` 比 `git_log` 多约 10 token/条，但换来
  的是压缩收益（见 benchmarks）。
- **DSH 自带文件/终端/搜索工具**：这些不经过 MCP，也就不会经过本代理；本插件
  只影响通过 MCP 接进来的 server。
- **已有真实 E2E 的 profile：git / filesystem / memory / sqlite / github**
  （2026-10-28，见 [e2e-verification.md §8](e2e-verification.md)）；`fetch` 还没有
  profile，接到它时代理只做透传（仍会注入 `retrieve_original`），不会压缩。
- **GitHub 需要两样东西**：官方 `github-mcp-server` 二进制（安装器会找
  `.e2e/tools/github/` 或 `MCP_GITHUB_SERVER_EXE`）与 token。token 不落配置：
  `scripts/dsh_entry.py` 在 `--server github` 时若环境没有 token，会用本机
  `gh auth token` 取一次；没有 gh 的话请在 MCP 行里显式配 `env`。

## 7. 与 ZCode 接入的差异

| | ZCode | DSH |
|---|---|---|
| 配置位置 | MCP 客户端 JSON（`mcp.servers`） | profile 的 `cordis.patch.yml` 插件行 |
| 启动命令 | 直接 `python zcode_entry.py ...` | `@deepseek-ai/dsh-mcp-client` 的 `command/args` |
| 工具命名 | server 原名 | `mcp__<serverName>__<tool>` |
| 环境 | GUI 拉起，环境极简 | harness 进程环境，仍建议走 `dsh_entry.py` |
| 重载 | 重启客户端 | HMR 可热重载（写完 patch 需注意） |