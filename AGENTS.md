# AGENTS.md — mcp-compress-proxy 项目规则

> 本文件是本项目唯一在 ZCode 长会话压缩（compaction）后仍原样保留的规则载体。
> 每个会话开始时、以及会话被压缩后，必须先重读本文件再继续工作。
> 发现新的边界或失败模式时，逐条**追加**到文末「Rules 摘要」一节，不要另建规则文件。

## 项目定位

任务感知的 MCP 透明压缩代理（Task-Aware MCP Compression Proxy），面向
ZCode / Claude Code / Cursor 等 MCP 客户端。

差异化：不是通用 JSON 压缩，而是根据**当前任务类型**（debug / review / explore）
选择不同的字段保留策略，对 git、filesystem、terminal 三类 MCP server 做专用压缩。

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
6. 第一版只支持 **git、filesystem、terminal** 三个 server，不做 GitHub / Postgres。
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

<!-- 阶段二起，新发现的边界与失败模式追加在此行之下 -->
