# 真实 MCP 端到端验证记录（E2E）

- 验证日期：2026-10-24 ~ 2026-10-28
- 执行方式：`scripts/e2e_mcp_git.py`（可重复运行，退出码 0=通过）
- 结论：**全部通过**——真实 `mcp-server-git`、`server-filesystem`、
  `server-memory` 直连与经压缩代理两条链路均验证成功；**ZCode 真实接入亦实测通过**
  （2026-10-25：connected、git_log 压缩、retrieve_original 完整还原，证据见 §5）；
  filesystem / memory 见 §8。

## 1. 版本矩阵

| 组件 | 版本 | 说明 |
|---|---|---|
| Windows | 10.0.26200 x64 | |
| Python | 3.12.9（<Python安装目录>；项目 venv 同源） | |
| mcp-server-git | **2026.10.19**（PyPI，固定版本） | 官方 git MCP server |
| mcp Python SDK | 2.3.0（venv 内，代理侧） | |
| uv / uvx | 0.12.13 | 拉起 mcp-server-git |
| git | 2.51.0.windows.1（<Git安装目录>\cmd） | mcp-server-git 经 GitPython 调用它 |
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
7. **本机 PATH**：ZCode agent 进程未继承 `<Node安装目录>`、`<Git安装目录>\cmd`
   （在注册表用户 PATH 中）→ E2E 脚本内置注册表 PATH 扫描兜底。

## 3. 实际执行的命令与结果摘要

```bash
# 直连 + 代理双链路（完整日志见 .e2e/logs/，断言逐条打印）
<PROJECT_DIR>/.venv/Scripts/python.exe scripts/e2e_mcp_git.py
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

配置：用户级 `<用户主目录>\.zcode\cli\config.json`（与工作区 `<工作区>\.zcode\config.json`
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
- ZCode 接入：删除 `<工作区>\.zcode\config.json` 中的 `git-compressed` 条目即可。

## 7. 已知限制 / 未验证项

- ZCode GUI 连接与调用（§5）待人工确认——agent 无法操作 GUI 与重启客户端。
- terminal 类 server 暂无官方参考实现，真实 E2E 未做；filesystem / memory 已于
  §8 完成（2026-10-28）。
- 代理链路中任务类型由工具参数文本推断（R9 弱信号），本 E2E 命中的是声明
  fallback 档；debug/review/explore 档在真实会话中的触发率未统计。


## 8. filesystem / memory / sqlite / github（2026-10-28）

脚本：`scripts/e2e_mcp_extra.py`（真实 npx / uvx server，直连 + 代理双链路）。

| 组件 | 版本 | 说明 |
|---|---|---|
| @modelcontextprotocol/server-filesystem | **2.0.1**（npm，固定） | 真实输出是 `[DIR]/[FILE]` 文本 |
| @modelcontextprotocol/server-memory | **2.0.0**（npm，固定） | 知识图谱，JSON 文本 |
| mcp-server-sqlite | 0.1.0（PyPI，uvx `--with 'mcp<2'`） | 输出是 Python repr，不是 JSON |
| github-mcp-server | **v2.0.3**（Go 官方二进制，stdio） | 46 个工具；需 token |
| Node.js | 24.15.0 | npx 拉起 |
| mcp Python SDK | 2.3.0 | 客户端侧 |

### 关键事实（实测）

1. **filesystem 的工具输出是纯文本**，不是 JSON：`list_directory` →
   `[DIR] name` / `[FILE] name` 行；`read_text_file` → 文件原文；
   `get_file_info` → `key: value` 文本。v1.0 profile 按合成 JSON 写，
   所以在真实 server 上**从未触发压缩**。v1.1 用 `text_rules.tools` 作用域区分
   “读文件 / 列目录”，并对状态类工具 `skip_compression_if` 透传。
2. **structuredContent 必须一起压缩**：filesystem / memory 在 text 之外还返回
   `structuredContent` 副本；只压 text 会让完整副本留在结果里，token 收益归零。
   代理现在只在 text 确实被压缩后，对 dict 形态的 structuredContent 套同一 profile
   （fail-open；列表形态暂不处理）。
3. **memory 的 `read_graph` 是 JSON 文本**（`{entities, relations}`，实体含
   `observations[]`）→ 解析成 dict 后走 retain 白名单，实体名/关系全保留、
   observations 截断。`create_entities` 等写操作走 skip 规则原样透传。
4. **error_anchor / error_only 支持 fallback**：没命中任何错误行时按声明的
   `fallback: {mode: head_tail, head: N, tail: M}` 兜底，避免整字段被清空后
   只能原样透传（filesystem 读文件需要）。
5. **sqlite 的输出是 Python repr，不是 JSON**：`read_query` 返回
   `[{'id': 1, 'name': 'item0'}]`（单引号）。sqlite profile 声明
   `parse_python_literals: true`，代理用 `ast.literal_eval` 解析后压缩，但 SQLite
   里存的是**原始文本**（`ref_payload={"output": text}`），retrieve 逐字节还原。
6. **数组载荷需要 `array_envelope`**：profile 声明 `array_envelope: rows` 后，
   数组先包成 `{"rows": [...]}` 再套 retain（`rows[0:50]`），输出是
   `{"rows": [...], "sw_ref": ...}`；行数没被裁掉时原样透传，不套信封。
7. **mcp-server-sqlite 0.1.0 与 mcp 2.x 不兼容**：它调用已被删除的
   `Server.list_resources`，启动即 `AttributeError`。必须用
   `uvx --with "mcp<2" mcp-server-sqlite`（DSH 安装器已内置）。
8. **GitHub MCP 的工具面很大（46 个）且形状各异**：列表接口是 JSON 数组或
   `{total_count, items[]}`，写入接口是短确认。profile 用 `compress_only_tools`
   只压 issue/PR/commit/repo 列表这几个高噪音读工具，其余一律透传——否则白名单
   会把未知形状清空。`list_commits` / `list_pull_requests` 是顶层数组，走
   `array_envelope: rows`。
9. **GitHub token 不落配置文件**：`scripts/dsh_entry.py` 在 `--server github` 时，
   若环境没有 `GITHUB_PERSONAL_ACCESS_TOKEN`，用本机 `gh auth token` 取一次
   （DSH 会滤掉环境里 TOKEN 形状的变量，不能指望全局变量透传）。


### 实测量化（本次运行）

| 场景 | 直连 | 经代理 | 说明 |
|---|---|---|---|
| `list_directory`（220 个文件） | 4409 chars / 220 行 | 2904 chars / 142 行 | fallback 头 100 + 尾 40 |
| `read_text_file`（3000 行源码） | 147598 chars | 5800 chars | 头 60 + 尾 60，约 25× |
| `read_graph`（6 实体 × 6 观察 + 5 关系） | 8643 chars | 1676 chars | 3 实体 × 2 观察 + 3 关系（fallback） |
| `read_query`（120 行 × 3 列） | 19822 chars（Python repr） | 8274 chars（JSON 信封） | fallback 保留前 50 行 |
| GitHub `list_issues`（cli/cli 20 条） | 30989 chars | 421 chars | fallback 3 条 + body 首行 |
| GitHub `list_pull_requests`（10 条） | 62036 chars | 504 chars | rows 信封 + 3 条 |
| GitHub `list_commits`（20 条） | 20418 chars | 559 chars | rows 信封 + 3 条 |
| GitHub `search_repositories`（10 条） | 5811 chars | 190 chars | 3 条元数据 |
| GitHub PR diff（PR #14846，205 行） | 10763 chars | 8640 chars | head 120 + tail 40（文本 diff） |

### 断言（全部 PASS）

- 直连：filesystem 返回 `[FILE]` 行 / 完整文件内容；memory 6 实体、6 观察、5 关系。
- 经代理：
  - `tools/list` 含 `retrieve_original`；
  - 列表 / 大文件 / 图谱均带 `sw-ref:`（文本形态另有 `sw-compressed` 标记）；
  - `structuredContent` 同步含 `sw_ref`（不再是完整副本）；
  - `get_file_info` / `create_entities` 原样透传（skip 规则）；
  - `retrieve_original` 完整还原（列表、147KB 文件、7 实体图谱逐字节一致）；
  - sqlite：`read_query` 输出 `{"rows": [50 行], "sw_ref": ...}`，retrieve 还原出
    **原始 Python repr 文本**（与直连逐字符一致）；`retrieve_original` 也支持
    `field_path="57.name"` 直接取某行某列；`list_tables` 走 skip 规则原样透传。
  - github：`list_issues` / `list_pull_requests` / `list_commits` / `search_repositories`
    均带 `sw_ref`，fallback 各保留 3 条；`get_me` 走 `compress_only_tools` 之外的
    透传路径（不套信封）；`retrieve_original` 还原完整 issues 文本；
    `pull_request_read` 的 diff 文本被压缩（method=get 的 JSON 逐字符透传）。


复跑：

```powershell
.venv\Scripts\python.exe scripts\e2e_mcp_extra.py
# → ALL PASS: filesystem + memory + sqlite (direct + proxy + retrieve)

.venv\Scripts\python.exe scripts\e2e_mcp_github.py
# → ALL PASS: github (direct + proxy + retrieve)   （无 token/二进制时打印 SKIP）
```