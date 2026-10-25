# fixtures

真实 MCP server 输出样本（阶段四使用）。计划清单：

- `git_log_debug.json` — 20 条 commit 的 `git log --stat` 输出
- `git_log_review.json` — 30 条 commit 的 `git log` 输出
- `git_diff_large.json` — 15 个文件变更的 `git diff` 输出
- `fs_list_large.json` — 递归 `list_dir` 的 200 个文件条目
- `fs_read_large.txt` — 3000 行源码文件的读取结果
- `terminal_test_output.txt` — 失败 pytest 运行的完整 stdout + stderr
- `terminal_build_log.txt` — npm build 完整日志

不足的样本可用脚本生成模拟数据，但 benchmark 报告中必须标注 synthetic / real。
