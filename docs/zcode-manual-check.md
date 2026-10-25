# ZCode 手动接入确认清单（2 分钟）

> 前置已全部就绪：配置 `E:\GLM\.zcode\config.json`、demo 仓库
> `E:\GLM\mcp-compress-proxy\.e2e\demo-repo`（21 commits）、uv 缓存已热。
> 你只需要：重启 ZCode → 确认连接 → 调两次工具 → 截 3 张图。

## 1. 完全退出 ZCode

托盘图标右键 → 退出（只关窗口进程可能还在）。

## 2. 重新打开 ZCode，工作区必须是 `E:\GLM`

配置文件在 `E:\GLM\.zcode\config.json`（工作区级），打开别的文件夹不会加载。

## 3. 确认 MCP 连接

设置 → MCP：`git-compressed` 应显示 connected（首次拉起约 3–10 秒）。
**截图 1**。若失败见 §6 排障。

## 4. 调 git_log 验证压缩（新会话，原样粘贴）

```
请用 mcp 的 git-compressed 服务器调用 git_log 工具，参数：repo_path 填
E:\GLM\mcp-compress-proxy\.e2e\demo-repo，max_count 填 20。
把工具的原始输出完整贴出来，不要总结。
```

预期证据（两处都要有）：
- 中段省略标记：`... [N lines omitted (sw-compressed)] ...`
- 末尾标记行：`[sw-compressed; original: sw-ref:xxxxxxxxxxxx — call retrieve_original]`

**截图 2**。

## 5. 调 retrieve_original 验证还原（同会话继续）

```
把上一步输出末尾的 sw-ref: 引用 ID 取出来，调用 retrieve_original 工具，
参数 ref_id 传这个 ID，把返回的完整原始输出贴出来。
```

预期：被省略的 commit 全部回来（完整历史）。**截图 3**。

## 6. 连不上时（按顺序）

1. 确认工作区是 `E:\GLM`，且 `E:\GLM\.zcode\config.json` 存在。
2. 记下 MCP 面板显示的错误信息。
3. 开 cmd/PowerShell 直接跑（与 ZCode 无关的独立验证）：
   `E:\GLM\mcp-compress-proxy\.venv\Scripts\python.exe E:\GLM\mcp-compress-proxy\scripts\e2e_mcp_git.py`
   - 输出 `E2E ALL PASS` → 命令链没问题，是 ZCode 拉起环境的问题 →
     把 `E:\GLM\mcp-compress-proxy\.e2e\logs\proxy.stderr.log` 内容发出来。
   - 脚本报错 → 把最后 20 行发出来。

## 已知点

- git_log 不传 max_count 时默认只返回 10 条（官方行为），仍足够触发压缩。
- git_status 是短输出，原样透传、没有 sw-ref 标记——这是预期行为，不是故障。
- v2 修复（2026-10-24）：连接超时的根因是 ZCode GUI 子进程 PATH 无 git，
  GitPython import 即崩；现改为 `scripts\zcode_entry.py` 入口 + `uv tool install`
  固化的 `mcp-server-git.exe`，已用"空环境"冒烟验证（1.4s 握手，
  `python scripts\verify_zcode_chain.py` 可复跑）。旧配置里的 uvx/env 写法已废弃。
