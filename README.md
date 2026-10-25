# mcp-compress-proxy

任务感知的 MCP 透明压缩代理（Task-Aware MCP Compression Proxy）。

面向 ZCode / Claude Code / Cursor 等 MCP 客户端：代理拉起原始 MCP server
（第一版支持 git，filesystem / terminal profile 已就绪），按任务意图用 YAML Profile
驱动字段级压缩；被丢弃的数据落入 SQLite，可随时通过注入的 `retrieve_original`
工具无损取回（全量 / 子字段 / 行区间 / grep 部分取回）。

- 架构约束与迭代记录：见 [AGENTS.md](AGENTS.md)
- 合成基准：[tests/benchmark/report.md](tests/benchmark/report.md)（平均压缩率 81.5%、关键字段保留率 100%）
- 真实会话基准（20 个 LLM 会话 A/B 对照）：[v1](tests/benchmark/real_sessions_report.md) ·
  [v2（优化后）](tests/benchmark/real_sessions_report_v2.md)（retrieve 6→0，深扫任务 token 改善 31-34%，成功率无损）

## 快速开始

要求：Python ≥ 3.10（开发于 3.12）、[uv](https://docs.astral.sh/uv/)、git。

```bash
git clone https://github.com/LT326/mcp-compress-proxy.git
cd mcp-compress-proxy
uv venv && uv pip install -e ".[dev]"
python -m pytest tests/ -v          # 50 个测试应全绿
```

安装要代理的 server（以官方 git server 为例，固定版本）：

```bash
uv tool install "mcp-server-git==2026.10.19"   # 大陆网络可加 --index-url https://pypi.tuna.tsinghua.edu.cn/simple
```

> ⚠️ 2026-10 核验事实：npm 包 `@modelcontextprotocol/server-git` 已下架（registry E404）。
> 官方 git MCP server 现为 **PyPI 包 `mcp-server-git`**。详见
> [docs/e2e-verification.md](docs/e2e-verification.md)。

## 接入 MCP 客户端（以 ZCode 为例）

下面 `<PROJECT_DIR>` 指本仓库克隆路径，`<YOUR_REPO>` 指你要代理的 git 仓库。
写入 ZCode 的 MCP 配置（用户级或工作区级均可）：

```json
{
  "mcp": {
    "servers": {
      "git-compressed": {
        "command": "<PROJECT_DIR>\\.venv\\Scripts\\python.exe",
        "args": [
          "<PROJECT_DIR>\\scripts\\zcode_entry.py",
          "--server", "git",
          "--db", "<PROJECT_DIR>\\refs.db",
          "--",
          "mcp-server-git",
          "--repository", "<YOUR_REPO>"
        ]
      }
    }
  }
}
```

- **Windows 必须走 `scripts/zcode_entry.py` 入口**：GUI 客户端拉起子进程的环境往往极简
  （PATH 缺 git → server 内 GitPython 启动即崩 → 表现为连接 30 秒超时）。该入口会从
  注册表补全 PATH、写入 `GIT_PYTHON_GIT_EXECUTABLE` 绝对路径，再进入代理主循环。
  Linux / macOS 或环境完整的客户端可直接用 `-m mcp_compress_proxy.proxy` 替代该入口。
- `--db` 是 sw-ref 可逆存储的 SQLite 路径，可放任意位置。
- `mcp-server-git` 换成 `uv tool install` 装出的可执行文件绝对路径最稳（拉起零网络）。
- 接入其它 stdio MCP server：把 `--` 之后的子命令换成对应 server 的启动命令，
  并在 `profiles/` 下按 `profiles/{server_name}.yaml` 增加一份 Profile。
- 空环境冒烟验证（模拟 GUI 残缺环境）：

```bash
python scripts/verify_zcode_chain.py
```

## 使用行为

- **窄意图查询原样透传**（`git_show` / `git_diff` / `max_count≤20` 的 `git_log`）——
  不压缩、不注入标记；宽范围扫描（如 `max_count>20` 的 `git_log`）返回**块级摘要**：
  每条 commit 一行 `hash | 作者 | message 首行`，末尾带
  `[sw-compressed; original: sw-ref:… — call retrieve_original]` 标记。
- 需要原文时调用注入的 `retrieve_original`：全量
  `{"ref_id": "sw-ref:…"}`、行区间 `{"ref_id": …, "lines": "120-180"}`、
  过滤 `{"ref_id": …, "grep": "^commit"}`（命中行带行号）。
- 压缩规则全部在 `profiles/*.yaml`（声明式，改 YAML 不改代码）；终端类输出启用
  R5：错误信号（非零退出码 / Traceback / FAILED）无论任务类型 100% 保留。
- 代理 fail-open：压缩路径任何异常一律透传原始结果，不影响可用性。

## 真实 E2E（可复跑）

```bash
.venv\Scripts\python.exe scripts\e2e_mcp_git.py            # 直连 + 经代理双链路（真实 mcp-server-git）
set MCP_E2E=1 && .venv\Scripts\python.exe -m pytest tests\test_e2e_git.py -v
```

完整证据（版本矩阵、命令、输出、常见问题、清理）见
[docs/e2e-verification.md](docs/e2e-verification.md)；
ZCode 图形端手把手确认清单见 [docs/zcode-manual-check.md](docs/zcode-manual-check.md)
（docs 内含开发机的绝对路径，为原始验证记录，保留未改）。

## 适用边界（来自真实基准的诚实结论）

✓ 大输出、概览型消费（摸底 / 定位 / 列清单）、长会话——输出压到 1/4~1/8，几乎零取回；
× 精确逐条处理全量数据的任务（数数、全清单核对）会触发取回，净值可能为负——
对此类调用请用窄查询（透传）或部分取回。详见
[real_sessions_report_v2.md](tests/benchmark/real_sessions_report_v2.md) 结论一节。

## 开发

- Python ≥ 3.10；依赖管理用 uv：`uv venv && uv pip install -e ".[dev]"`
- 测试：`python -m pytest tests/ -v`（默认不含真实网络 E2E，`MCP_E2E=1` 开启）
- 真实会话基准工具链：`scripts/real_bench_prepare.py`（任务+ground truth 生成）、
  `scripts/mcp_call.py`（双臂 helper）、`scripts/real_bench_score.py`（计分报告）

## License

[MIT](LICENSE)
