# 真实 MCP 端到端验证记录（E2E）

- 验证日期：2026-10-24 ~ 2026-10-25
- 执行方式：`scripts/e2e_mcp_git.py`（可重复运行，退出码 0=通过）
- 结论：**全部通过**——真实 `mcp-server-git` 直连与经压缩代理两条链路均验证成功；
  **ZCode 真实接入亦实测通过**（2026-10-25：connected、git_log 压缩、retrieve_original
  完整还原，证据见 §5）。

## 1. 版本矩阵

| 组件 | 版本 | 说明 |
|---|---|---|
| Windows | 10.0.26200 x64 | |
| Python | 3.12.9（E:\python-312；项目 venv 同源） | |
| mcp-server-git | **2026.10.19**（PyPI，固定版本） | 官方 git MCP server |
| mcp Python SDK | 2.3.0（venv 内，代理侧） | |
| uv / uvx | 0.12.13 | 拉起 mcp-server-git |
| git | 2.51.0.windows.1（E:\Git\Git\cmd） | mcp-server-git 经 GitPython 调用它 |
| node/npm | **不需要**（旧方案 npm 包已死，见 §2） | |

## 2. 关键核验事实（不凭记忆，全部实测）

1. **npm 包 `@modelcontextprotocol/server-git` 已下架**：`npm view` 返回 E404
   （registry.npmjs.org 权威响应）。官方 TypeScript 参考 server 已归档至
   servers-archived；现行官方 git server 是 PyPI 包 `mcp-server-git`
   （GitHub issues #1200 / #2089 印证）。
2. **CLI 参数**（README + `--help` 实测）：`mcp-server-git -r, --repository PATH`，
   接受仓库内任意路径，启动时自动上溯到仓库根。
3. **工具清单**（tools/list 实测，12 个）：git_add, git_branch, git_checkout,
   git_commit, git_create_branch, git_diff, git_diff_staged, git_diff_unstaged,
   git_log, git_reset, git_show, git_status。
4. **`git_log` 的 `max_count` 默认只有 10**——必须显式传大值才能拿全量历史
   （首次 E2E 即栽在这里）。
5. **工具输出是纯文本**（`Repository status:` / `Commit history:` 格式），
   不是结构化 JSON → 代理按 `{"output": text}` 包装后套 git.yaml v1.1 的
   `output` 文本规则（head_tail），短输出不改写不注入 sw-ref。
6. **本机网络**：`uv` 直连 `pypi.org/simple` 超时 → 必须 `UV_INDEX_URL`
   指清华镜像；`pypi.org/pypi/*.json` 偶尔可达但不可依赖。
7. **本机 PATH**：ZCode agent 进程未继承 `E:\Node`、`E:\Git\Git\cmd`
   （在注册表用户 PATH 中）→ E2E 脚本内置注册表 PATH 扫描兜底。

## 3. 实际执行的命令与结果摘要

```bash
# 直连 + 代理双链路（完整日志见 .e2e/logs/，断言逐条打印）
E:/GLM/mcp-compress-proxy/.venv/Scripts/python.exe scripts/e2e_mcp_git.py
# → ===== E2E ALL PASS =====，退出码 0，临时仓库自动清理

# pytest 门控形式
set MCP_E2E=1 && .venv\Scripts\python.exe -m pytest tests/test_e2e_git.py -v
# → 1 passed in 18.42s

# 默认套件（不含 E2E，不依赖网络）
.venv\Scripts\python.exe -m pytest tests -q
# → 44 passed, 1 skipped in 0.95s
```

### 直连链路断言（全部 PASS）

| 步骤 | 断言 |
|---|---|
| initialize | serverInfo `mcp-git` v2026.10.19，protocol 2024-11-05，1.3s |
| tools/list | 12 工具与官方 README 一致 |
| git_status（只读1） | 显示 staged `src/notes.txt` + modified `src/app.py`，与临时仓库状态一致 |
| git_log（只读2，max_count=1000） | 62 commits 全量 7810 chars，首条 `c1: initial readme` 与末条 fixture 均可见 |
| git_diff_unstaged（只读3） | 含未暂存修改 `+extra e2e line` |
| git_commit（写） | 提交成功返回 hash；git_log 出现新 commit；notes.txt 不再处于 staged |

### 经代理链路断言（全部 PASS）

| 步骤 | 断言 |
|---|---|
| initialize / tools/list | 13 工具 = 12 原工具 + 注入的 `retrieve_original` |
| git_add + git_commit（写，经代理） | 成功，仓库状态真实变化 |
| git_status（短文本） | 原样透传，无 sw-ref 注入 |
| git_log（长文本 8488 chars） | **压缩为 1031 chars（86.8%）**：sw-ref 标记行 + 中段 `lines omitted (sw-compressed)` + 最新 commit 保留在头部窗口；中段 `fixture: change 30` 被裁 |
| retrieve_original(sw-ref) | 完整还原 8488 chars，被裁行 `fixture: change 30` 恢复 |
| SQLite | `--db` 文件非空，原始 payload 可回溯 |

## 4. 失败过的问题与修复（保留作排查参考）

| 现象 | 原因 | 修复 |
|---|---|---|
| `npm view @modelcontextprotocol/server-git` E404 | 包已下架 | 换 PyPI `mcp-server-git` + uvx |
| uvx `Failed to fetch pypi.org/simple` 超时 | 本机直连超时 | `UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple` |
| agent 内 `which node/git` 找不到 | ZCode 进程 PATH 精简 | 脚本扫描注册表 PATH（HKCU/HKLM） |
| 直连 git_log 断言首条 commit 失败 | `max_count` 默认 10 | 显式传 `max_count: 1000` |
| 代理 git_commit 报 "No changes staged" | 直连场景已提交唯一 staged 文件 | 代理场景先 `git_add` 再 `git_commit` |
| **ZCode 里连接 30s 超时循环** | ZCode GUI 子进程 PATH 无 git → GitPython import 即崩 | `uv tool install` 固化 exe + `zcode_entry.py` 入口（注册表 PATH 合并 + `GIT_PYTHON_GIT_EXECUTABLE` 绝对路径） |
| **ZCode 里 retrieve_original 报 "missing required resultType"** | MCP 2026-07-28 要求 tools/call 结果必须带 `resultType`（Literal `"complete"/"input_required"`，见 mcp_types/_types.py:160），代理本地生成的结果是老式裸结构 | `handle_retrieve` 结果增加 `"resultType": "complete"`；test_proxy_smoke 与 verify_zcode_chain 均加断言 |
| **修复后重试 retrieve 仍报同样错误（两次一字不差）** | MCP 页面的"刷新"**不会重启已存在的 server 子进程**；ZCode 里挂着两组修复前启动的旧代理链（旧进程跑内存里的旧代码） | `scripts/kill_stale_proxy.py` 清掉全部残留进程 + 完全退出 ZCode 重开；教训：改代理代码后必须杀旧进程或完全重启，光点刷新无效 |

## 5. ZCode 接入（2026-10-25 实测通过）

配置：用户级 `C:\Users\a\.zcode\cli\config.json`（与工作区 `E:\GLM\.zcode\config.json`
同内容，同名时用户级优先）：venv python → `scripts/zcode_entry.py`（环境修复入口）
→ 代理 → 固化版 `mcp-server-git.exe` → `.e2e\demo-repo`（21 commits）。

实测结果（会话内原始输出留档于验证会话）：

| 步骤 | 结果 |
|---|---|
| MCP 页面连接 | ✅ connected（v2 链路，秒级握手） |
| git_log（max_count=20） | ✅ 压缩生效：仅 6 条 commit + `[80 lines omitted (sw-compressed)]` + 末尾 `[sw-compressed; original: sw-ref:7456acb48147 — call retrieve_original]` |
| retrieve_original(sw-ref:7456acb48147) | ✅ 完整还原 20 条 commit 历史（note 1 ~ note 20 全部在场，被省略段完整恢复） |

备注：纯文本 payload 在代理侧以 `{"output": text}` 包装入库，因此取回时外层带
`"output"` 键——行为正确；v1.2 可考虑对单键 output 载荷解包美化。

## 6. 清理方式

- E2E 临时仓库：成功自动删除；失败保留（路径打印在 `[diag]` 行），手动
  `rmdir /s /q <路径>`。
- `.e2e/`：`uv-cache`（uv 环境缓存，可删，下次重建约 30s）、`logs/`（server
  stderr 日志）、`zcode_refs.db`（ZCode 链路的 sw-ref 库）、`demo-repo`（演示仓库）。
- ZCode 接入：删除 `E:\GLM\.zcode\config.json` 中的 `git-compressed` 条目即可。

## 7. 已知限制 / 未验证项

- ZCode GUI 连接与调用（§5）待人工确认——agent 无法操作 GUI 与重启客户端。
- filesystem / terminal 两类 server 的真实 E2E 未做（官方同为 PyPI 包，链路同构，
  可按本脚本模式扩展）。
- 代理链路中任务类型由工具参数文本推断（R9 弱信号），本 E2E 命中的是声明
  fallback 档；debug/review/explore 档在真实会话中的触发率未统计。
