# 发布到 PyPI / npm / DSH 插件市场

> 当前状态（2026-10-28）：**代码与打包已就绪，发布被凭证阻塞**——本机没有 npm 登录，
> 也没有 PyPI token；GitHub 仓库是公开的（LT326/mcp-compress-proxy）。
> 本文件是拿到凭证后的一条龙步骤，全部命令都可在仓库根目录执行。

## 0. 一次性检查

```powershell
.venv\Scripts\python.exe scripts\publish_release.py
```

它会：

- 校验 pyproject 与 `integrations/dsh/package.json` 版本一致；
- `uv build` 构建 wheel/sdist，并断言 wheel 里带 6 份 `profiles/*.yaml`
  （`share/mcp-compress-proxy/profiles`，loader 已在 pip 安装后可回退到该目录）；
- `npm pack --dry-run` 校验插件包只含 4 个文件；
- 报告 PyPI token / npm 登录状态；
- 打印插件市场条目文本。

## 1. PyPI（Python 包）

```powershell
# 一次性：在 https://pypi.org/manage/account/token/ 建 token
$env:UV_PUBLISH_TOKEN = "pypi-..."
.venv\Scripts\python.exe scripts\publish_release.py --yes --skip-npm
```

或手工：

```powershell
uv build --out-dir .e2e\dist
uv publish .e2e\dist          # 需要 UV_PUBLISH_TOKEN
```

验证：

```powershell
uv venv .e2e\venv-check
uv pip install --python .e2e\venv-check\Scripts\python.exe mcp-compress-proxy
.\.e2e\venv-check\Scripts\python.exe -c "from mcp_compress_proxy.profiles.loader import DEFAULT_PROFILES_DIR as D; print(D, len(list(D.glob('*.yaml'))))"
# → <venv>\share\mcp-compress-proxy\profiles 6
```

> 注意：包里**不含** `profiles/` 之外的运行数据；`retrieve_original` 的 SQLite 默认落在
> `MCP_COMPRESS_DB` 或包目录旁的 `.sw_refs.db`，DSH 接入建议显式传 `--db`。

## 2. npm（DSH 插件包）

```powershell
npm login                      # 或：npm config set //registry.npmjs.org/:_authToken=...
.venv\Scripts\python.exe scripts\publish_release.py --yes --skip-pypi
```

或手工：

```powershell
cd integrations\dsh
npm publish --access public
cd ..\..
```

验证：

```powershell
npm view dsh-plugin-mcp-compress-proxy version
dsh plugin --profile web add dsh-plugin-mcp-compress-proxy   # 有 dsh CLI 时
```

> 本机桌面版没有全局 `dsh`，可用
> `ELECTRON_RUN_AS_NODE=1 "<DSH_INSTALL>\DeepSeek Harness.exe" "<DSH_INSTALL>\resources\app.asar\dsh\node_modules\@deepseek-ai\dsh\lib\bin.js"`。

## 3. 插件市场（awesome-dsh-plugin）

市场列表 = [awesome-dsh-plugin](https://github.com/awesome-dsh-plugin/awesome-dsh-plugin)。
发布 npm 包之后，在它的 README 里 `## Plugins → ### Tools & Capabilities` 下加一行：

```markdown
- [LT326/mcp-compress-proxy](https://github.com/LT326/mcp-compress-proxy) - Reversible compression proxy for MCP tool output: git, GitHub issue/PR/commit lists, filesystem reads, memory graphs and SQLite queries shrink 25-99%, with an injected retrieve_original tool for exact recovery.
```

然后提 PR：

```powershell
gh repo fork awesome-dsh-plugin/awesome-dsh-plugin --clone
cd awesome-dsh-plugin
git checkout -b add-mcp-compress-proxy
# 编辑 README.md，插入上面那一行
git add README.md
git commit -m "Add mcp-compress-proxy"
git push -u origin add-mcp-compress-proxy
gh pr create --title "Add mcp-compress-proxy" --body "Reversible compression proxy for MCP tool output (mcp__<server>__* tools, ~25-99% smaller with retrieve_original)."
```

评审会实际安装并核对描述；被拒不会影响 npm 包本身。

## 4. 发布前必须完成的两件事

1. **把代码推到 GitHub**：市场条目指向仓库，`dsh plugin add github:...`（备用路径）
   也要求仓库根有 `package.json`。当前改动还未 commit/push。
2. **确认 Python 依赖可安装**：插件行默认 `python -m mcp_compress_proxy.proxy`；
   PyPI 发布后用户 `pip install mcp-compress-proxy` 即可。

## 5. 现在的硬阻塞

| 步骤 | 需要 | 本机状态 |
|---|---|---|
| PyPI | `UV_PUBLISH_TOKEN` | 未配置 |
| npm | `npm login` | 未登录（`npm whoami` 报 ENEEDAUTH） |
| 市场 PR | npm 包已发布 + 仓库已推送 | 等待前两步 |

登录后跑 `scripts/publish_release.py --yes` 即可一次发完 PyPI + npm；
市场 PR 按 `3` 执行。
