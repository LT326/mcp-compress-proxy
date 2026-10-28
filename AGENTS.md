# AGENTS.md — mcp-compress-proxy 项目规则

> 本文件是本项目唯一在 ZCode 长会话压缩（compaction）后仍原样保留的规则载体。
> 每个会话开始时、以及会话被压缩后，必须先重读本文件再继续工作。
> 发现新的边界或失败模式时，逐条**追加**到文末「Rules 摘要」一节，不要另建规则文件。

## 项目定位

任务感知的 MCP 透明压缩代理（Task-Aware MCP Compression Proxy），面向
ZCode / Claude Code / Cursor 等 MCP 客户端。

差异化：不是通用 JSON 压缩，而是根据**当前任务类型**（debug / review / explore）
选择不同的字段保留策略，对 git、filesystem、terminal、memory、sqlite、github 六类 MCP server 做专用压缩。

## 不可协商的架构约束（共 7 条）

1. 压缩逻辑由 **YAML Profile 声明式配置**驱动，禁止把压缩规则硬编码在 Python 类中。
2. 每个 MCP server 一个 Profile 文件，路径固定为 `profiles/{server_name}.yaml`。
3. 必须实现**可逆检索**：压缩后的 payload 携带 `sw-ref:` 前缀的引用 ID，原始数据
   写入 SQLite，通过注入的 `retrieve_original(ref_id, field_path=None)` 工具还原。
4. 必须实现 **Task Context Detector**：从最近 3—5 轮对话文本中，用关键词规则
   （不调用 LLM）推断任务类型，输出 `debug | review | explore | unknown`。
5. 代理层必须是**透明 stdio 代理**：客户端指向代理的 command，代理 spawn 原始
   MCP server 子进程，转发请求，仅修改 `tools/call` 的响应（以及 `initialize` /
   `tools/list` 返回的工具列表，用于注入 `retrieve_original`）。
6. 支持 **git、filesystem、terminal、memory、sqlite、github** 六个 server
   （memory / sqlite / github 于 2026-10-28 经用户批准加入）；Postgres 仍不做。
   新增 server 必须同时交付 Profile + 测试 + 真实 E2E，不允许只写 Profile 就宣称“支持”。
7. 所有对原始 payload 的修改必须记录在 `transform_manifest` 中，保证 write-back
   操作的 lossless 逆向重建。

## 目录约定

```
mcp-compress-proxy/
├── AGENTS.md                  # 本文件
├── pyproject.toml
├── src/mcp_compress_proxy/
│   ├── proxy/                 # stdio 透明代理主循环
│   ├── profiles/              # YAML Profile 加载器
│   ├── compressor/            # 按 Profile 执行字段保留/丢弃
│   ├── task_context/          # Task Context Detector
│   ├── store/                 # SQLite 可逆存储
│   └── tools/                 # 注入的 retrieve_original 工具定义
├── profiles/                  # 每个 server 一份 YAML Profile
│   ├── git.yaml
│   ├── filesystem.yaml
│   ├── memory.yaml
│   ├── sqlite.yaml
│   ├── github.yaml
│   └── terminal.yaml
└── tests/
    ├── fixtures/              # 真实 MCP 输出样本（阶段四）
    └── benchmark/             # 压缩率/保留率/retrieve 率基准（阶段四）
```

## 阶段边界与当前状态

- 阶段一（已完成，2026-10-24）：项目骨架 + 本规则文件 + smoke 测试。
- 阶段二（已完成，2026-10-24）：Task Context Detector + Profile 加载器 + 压缩引擎，6+3 个测试通过。
- 阶段三（已完成，2026-10-24）：stdio 代理主循环 + retrieve_original 注入，集成 smoke 通过。
- 阶段四（已完成，2026-10-24）：三份 v1.0 Profile + benchmark + 29 项回归测试。
  实测 28 组合：平均压缩率 81.5%（目标 ≥65%）、关键字段保留率 100%、retrieve 率 3.6%（目标 <15%），
  报告见 `tests/benchmark/report.md`。

四个阶段经用户批准在同一会话内连续完成（原设计为每阶段独立会话）。后续迭代在此更新状态。
- 真实 E2E（已完成，2026-10-24）：官方 mcp-server-git==2026.10.19 直连+代理双链路全过，
  详见 docs/e2e-verification.md；ZCode 真实接入亦实测通过（2026-10-25：connected、
  git_log 压缩、retrieve_original 完整还原；期间修掉三个真 bug，见 docs §4 与 R15/R16）。
- DeepSeek Harness 接入（已完成代码与自检，2026-10-28）：新增
  `integrations/dsh/` bundle + `scripts/install_dsh_plugin.py`（写 profile 的
  cordis 受管块）+ `scripts/verify_dsh_chain.py`（按 DSH spawn 方式走官方 MCP SDK
  2026-07-28 验证）。实测：handshake → tools/list（retrieve_original）→ git_log 压缩
  → retrieve_original 完整还原，resultType=complete。server 面未变（仍 git /
  filesystem / terminal；随后另加 memory，见下条）。详见 docs/dsh-integration.md 与 R19。
- filesystem 真实化 + memory 接入（2026-10-28）：filesystem profile v1.1 对齐真实
  文本输出（[DIR]/[FILE] 行）并用 text_rules.tools 区分读文件/列目录；新增 memory
  profile（知识图谱 JSON retain）；代理同步压缩 structuredContent；E2E 实测 147KB
  源码 → 5.8KB、220 行目录 → 142 行、知识图谱 8.6KB → 1.7KB，全部可 retrieve 完整
  还原（docs/e2e-verification.md §8，tests/test_profiles_real_shapes.py）。
- sqlite + 数组载荷（2026-10-28）：新增 profiles/sqlite.yaml；Profile 增加
  array_envelope / parse_python_literals；代理支持 JSON 数组与 Python repr 数组，
  输出 {"rows": [...], "sw_ref": ...}；E2E 实测 120 行查询 19822→8274 chars，
  retrieve 还原原始 Python repr 文本（docs §8，tests/test_array_payloads.py）。
- GitHub 接入（2026-10-28）：官方 github-mcp-server v2.0.3（Go，stdio）；新增
  profiles/github.yaml 与 compress_only_tools 白名单；E2E 实测 issues 30989→421、
  PRs 62036→504、commits 20418→559、repos 5811→190 chars，均可 retrieve 还原
  （docs §8，tests/test_github_profile.py）。

## Rules 摘要（长会话中必须重复声明）

> ZCode 会话压缩后，Agent 唯一确定能看到的规则就是本节。被压缩后先逐条重申，再继续工作。

- R1 压缩规则只存在于 `profiles/*.yaml`；调整压缩行为 = 改 YAML，不改 Python。
- R2 任何被丢弃的数据必须先落入 SQLite（`sw-ref:` 可回溯），不允许直接丢。
- R3 `task_type == unknown` 时走 fallback：保留顶层键 + 每个数组前 3 项，其余入 ref。
- R4 token 估算用「字符数 / 4」，禁止引入 tokenizer 依赖。
- R5 terminal 输出的错误信号（非零退出码、`Traceback`、`FAILED` / `ERROR` 块）
  无论 task_type 一律 100% 保留。
- R6 `retrieve_original` 由代理自身处理并直接返回，绝不转发给子进程。
- R7 子进程 stdout/stderr 必须实时转发，禁止缓冲到子进程退出。
- R8 不新建规则文件；新规则、新边界一律追加到本节末尾。
- R9 代理上下文边界：Detector 在代理链路中只能看到 tools/call 参数文本，看不到对话
  原文 → unknown / 声明 fallback 的触发率会偏高；接入新 server 必须先写声明 fallback。
- R10 白名单语义：task profile 存在 retain 时，压缩结果只含 retain 命中路径；
  YAML 中的 drop 列表是声明性说明，真正生效的是 retain 白名单。
- R11 text_rules.field 支持路径表达式（如 `commits[*].message`）；可用模式：
  error_anchor / head_tail / error_only / first_line / drop。
- R12 fail-open：代理内压缩路径任何异常一律透传原始结果，禁止因压缩失败断链。
- R13 MCP 的 initialize 结果不含工具列表；retrieve_original 的真正注入点是
  tools/list（initialize 已做防御式兼容，个别 server 违例带 tools 时同样注入）。
- R14 真实 server 事实（2026-10-24 实测）：官方 git MCP server 是 PyPI 包
  `mcp-server-git`（npm 包已死，E404）；`git_log` 的 `max_count` 默认仅 10；
  工具输出为纯文本 → 代理走 {"output": text} 包装 + git.yaml 的 output 文本规则；
  本机 uv 必须配 UV_INDEX_URL 清华镜像。复现命令见 docs/e2e-verification.md。
- R15 ZCode GUI 接入（2026-10-24 实测）：GUI 拉起 stdio MCP 子进程的环境极简
  （PATH 无 git/powershell，日志 spawn ENOENT）→ GitPython import 即崩 → 连接超时。
  接入必须走 `scripts/zcode_entry.py` 入口（注册表 PATH 合并 +
  GIT_PYTHON_GIT_EXECUTABLE 绝对路径 + TEMP 兜底），server 用
  `uv tool install` 固化 exe（拉起零网络）。空环境冒烟：scripts/verify_zcode_chain.py。
- R16 MCP 2026-07-28（2026-10-24 ZCode 实测踩中）：客户端按新修订版校验，
  tools/call 结果**必须**含 `resultType`（Literal "complete" / "input_required"，
  早期修订版缺省视为 complete）。代理本地生成的任何工具结果都要带
  `"resultType": "complete"`；转发真实 server 的结果原样透传不加工。
- R17 真实会话基准（2026-10-25，tests/benchmark/real_sessions_report.md）：10 任务 × 2 臂
  真实 LLM 会话（flask 仓库，双臂对称，ground truth 独立计算）——任务成功率无损
  （9/10 vs 9/10），但会话总 token +22.8%：真实 retrieve 率 60%（vs 合成基准 3.6%），
  "精确全量型"任务必然取回。**可逆压缩的价值边界 = 大输出 + 概览型消费 + 长会话**。
  期间修复 asyncio StreamReader 64KB 单行上限崩溃（spawn 传 limit=64MB，大 diff 的
  单行 JSON-RPC 响应会触发 LimitOverrunError 打崩代理）。
- R18 P0 优化与复测（2026-10-25，tests/benchmark/real_sessions_report_v2.md）：
  ①skip_compression_if——窄意图调用（git_show/git_diff、max_count≤20 的 git_log）
  原样透传不压缩不入库；②retrieve_original 支持 lines 行区间/grep 部分取回（文本
  包装载荷自动解包）；③commit_digest——git_log 文本块级摘要（每条
  `hash | 作者 | message首行`，非日志文本回退 head_tail）。复测：retrieve 6→0、
  成功率 10/10 vs 10/10、深扫任务 token 改善 31-34% 且持平/优于直连；总 token
  +15.6% 的剩余差距经分解为三个 n=1 行为方差（剔除后 -0.8% 持平）。



- R19 DeepSeek Harness 接入（2026-10-28 实测，DSH 0.2.1-rc.3）：
  ①DSH 没有独立 MCP 配置段——一个 MCP server 就是 profile `cordis.patch.yml` 里
  的一条 `@deepseek-ai/dsh-mcp-client` 插件行（`serverName/transport/command/args`），
  `insert` 列表插入；工具名是 `mcp__<serverName>__<tool>`，serverName 必须匹配
  `[A-Za-z0-9_-]{1,32}` 且同 scope 唯一。
  ②profile 目录 = `$DSH_PROFILE_DIR`（本机 `~/.dsh/profiles/desktop`）；`dsh` CLI 不在
  PATH 上，但可用 `ELECTRON_RUN_AS_NODE=1 <app>\DeepSeek Harness.exe
  <app.asar>\dsh\node_modules\@deepseek-ai\dsh\lib\bin.js` 调用（`--dump-config`
  可用，但 `--profile desktop` 被 Electron 应用独占而拒绝）；`dsh plugin` 只转发
  给 pnpm，不改 `dsh.profile.bundles`。配置默认 HMR 热重载 → 写 patch 会触发
  重新组合，必须在会话之间执行。
  ③DSH 的 stdio 客户端**先起一个临时 probe 进程再起 serving 进程**，代理必须能
  被重复握手；不要假设进程只被拉起一次。
  ④`cordis.patch.yml` 方言是 js-yaml JSON_SCHEMA + `!!js`（**仅标量**）：
  `!!js 'expr'` 在加载时用 `with(ctx)` 求值，`process` 可用；`args: !!js '[...]'`
  也可以（表达式求值成数组），但 `!!js` 不能直接标注序列节点。
  ⑤接入进程链固定为 `python scripts/dsh_entry.py → mcp_compress_proxy.proxy →`
  真实 server；`dsh_entry.py` 复用 `zcode_entry._patch_env`（R15 的 PATH/git 修复）。
  ⑥自检命令 `scripts/verify_dsh_chain.py`（用 mcp 2.3.0 客户端，LATEST=2026-07-28；
  实测 mcp-server-git 协商到 2025-11-25）。DSH 端工具列表出现即接入成功；
  `retrieve_original` 的 `resultType` 仍须为 `complete`（R16）。
  ⑦安装器 `scripts/install_dsh_plugin.py` 只改 `# >>> mcp-compress-proxy` 受管块，
  幂等、写前备份；静态 bundle 在 `integrations/dsh/`，靠环境变量启用，不烘死路径。



- R20 filesystem / memory 真实化（2026-10-28 实测）：
  ①真实 filesystem server（npm @modelcontextprotocol/server-filesystem 2.0.1）输出是
  纯文本（[DIR] name / [FILE] name / 文件原文 / key: value），v1.0 JSON profile 在
  真实 server 上从未触发压缩；v1.1 用 text_rules.tools 作用域（读文件 vs 列目录）+
  skip_compression_if（get_file_info / write_file 等）对齐真实形态。
  ②text_rules 新增两个声明式能力：tools / exclude_tools（同一 server 内按工具区分）
  与 fallback（error_anchor / error_only 未命中时回退 head_tail）。二者都只在 YAML
  声明，引擎不硬编码；compress(..., tool=...) 为可选参数，tool=None 时不做工具限制
  （benchmark / 旧调用保持原行为，见 tests/test_profiles_real_shapes.py）。
  ③structuredContent 必须与 text 一起压缩：filesystem / memory 会同时返回 text 与
  结构化副本，只压 text 会让完整副本留在结果里，token 收益归零。代理仅在 text 确实被
  压缩后，对 dict 形态的 structuredContent 套同一 profile；失败 fail-open。
  ④memory server（npm @modelcontextprotocol/server-memory 2.0.0）的 read_graph /
  search_nodes / open_nodes 返回 {entities:[{name,entityType,observations[]}],
  relations:[{from,to,relationType}]} JSON 文本；写操作用 skip 规则透传。
  ⑤实测量化（scripts/e2e_mcp_extra.py，2026-10-28）：list_directory 4409→2904 chars
  （220→142 行）、read_text_file 147598→5800 chars（约 25×）、read_graph 8643→1676
  chars；三者 retrieve_original 均逐字节还原。



- R21 sqlite / 数组载荷（2026-10-28 实测）：
  ①mcp-server-sqlite（PyPI 0.1.0）输出是 **Python repr**（`[{'id': 1, ...}]`，单引号），
  不是 JSON；read_query / list_tables / describe_table 都是。仅当 profile 声明
  `parse_python_literals: true` 时代理才 `ast.literal_eval`；SQLite 里仍存**原始文本**
  （`compress(..., ref_payload={"output": text})`），retrieve 逐字节还原。
  ②顶层数组载荷：profile 声明 `array_envelope: <key>` 后，engine 把 list 包成
  `{<key>: [...]}` 再套 retain；输出是 `{"<key>": [...], "sw_ref": ...}`（数组本身
  放不下 sw_ref，信封是必要代价）。未声明 envelope 的 profile 遇到数组一律透传（保守）。
  ③行数没被裁掉时不改写：避免给 3 行结果套信封 + sw_ref（proxy 里比较 rows 长度）。
  ④`compress()` 新增可选 `ref_payload`（存什么）与 `tool`（工具作用域）；
  `_canonical` / `make_ref_id` 接受 list；manifest 的 dropped_paths 去掉 envelope
  前缀（`rows[3].id` → `[3].id`），与原始数组下标对齐。
  ⑤mcp-server-sqlite 0.1.0 与 mcp 2.x 不兼容（调用已删除的 `Server.list_resources`，
  启动即 AttributeError）→ 必须 `uvx --with "mcp<2" mcp-server-sqlite`；DSH 安装器
  `resolve_server("sqlite")` 已内置这一参数。
  ⑥实测（scripts/e2e_mcp_extra.py）：120 行 × 3 列 19822 chars（Python repr）→ 8274
  chars（JSON 信封，fallback 前 50 行）；retrieve 默认与直连文本逐字符一致，
  也可以 `field_path="57.name"` 直接取某行某列（文本包装内部按 repr 解析）；
  `list_tables` 走 skip 规则原样透传。



- R22 GitHub 接入 / compress_only_tools（2026-10-28 实测）：
  ①官方 server 是 Go 二进制 `github/github-mcp-server`（v2.0.3，`stdio` 子命令），
  46 个工具、输出形状随工具而异（列表可能是顶层数组，也可能是
  `{total_count, items[]}` / `{issues: []}`）。安装方式：
  `gh release download v2.0.3 -R github/github-mcp-server
  -p github-mcp-server_Windows_x86_64.zip -D .e2e/tools/github` 后解压；
  `.e2e/` 已 gitignore，安装器可用 `MCP_GITHUB_SERVER_EXE` 覆盖。
  ②工具面大的 server 必须用 `compress_only_tools`：未列出的工具一律透传，
  避免白名单把未知形状清空（GitHub 只压 list_issues / search_issues /
  search_pull_requests / search_repositories / list_pull_requests / list_commits）。
  ③`list_commits` / `list_pull_requests` 是顶层数组 → 走
  `array_envelope: rows`；issue/PR/commit 的 `body` / commit.message 是主要噪音，
  debug 留前 25 行、review/explore 只留首行。
  ④Token：DSH 的 mcp-client 会滤掉环境里 `/KEY|PASSWORD|SECRET|TOKEN/` 形状的变量，
  所以全局 `GITHUB_PERSONAL_ACCESS_TOKEN` 不一定能透传；`scripts/dsh_entry.py`
  在 `--server github` 且环境无 token 时执行 `gh auth token` 取一次（不落配置）。
  没有 gh 的用户需要在 MCP 行里显式配 `env`。
  ⑤代理现在对**所有文本内容**都按 `{"output": text}` 存原文（JSON 也存），
  retrieve 默认返回与 server 逐字符一致的文本；`field_path` 由 retrieve 内部
  解析 JSON / Python repr 后取（文本包装增强）。结构化 `structuredContent` 仍存结构。
  ⑥实测（scripts/e2e_mcp_github.py，cli/cli）：list_issues 30989→421 chars、
  list_pull_requests 62036→504、list_commits 20418→559、search_repositories 5811→190；
  retrieve 还原完整 issues 文本，get_me 原样透传。



- R23 多形态工具 / text_only_tools（2026-10-28）：
  ①一个工具返回多种形态时（GitHub `pull_request_read`：method=get 返回 JSON、
  method=get_diff 返回文本 diff），不能只用 compress_only_tools——JSON 会走白名单
  被清空。Profile 的 `text_only_tools` 声明后，代理对该工具的 JSON dict/list 跳过
  压缩（原样透传），只走 output 文本规则，于是 diff 能压、JSON 不受影响。
  ②github.yaml：`pull_request_read` 同时进 compress_only_tools 与 text_only_tools；
  diff 规则按任务 head_tail（debug 200/50、review 150/50、explore 80/20、fallback
  120/40），压缩后带 `sw-compressed` 标记，retrieve 还原完整 diff。
  ③实测（cli/cli PR #14846，205 行 diff）：10763→8640 chars（该 diff 较小；行数越多
  收益越大，极端大 diff 可到 90%+）。method=get 的 JSON 逐字符透传。
  ④未做的部分：`pull_request_read` 的 get_files / get_comments 等 JSON 形态仍透传；
  要做需要按参数路由 retain（目前只有文本路径按工具区分）。

<!-- 阶段二起，新发现的边界与失败模式追加在此行之下 -->