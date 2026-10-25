# benchmark

压缩基准（阶段四实现）：`run_benchmark.py` + `report.md`。

对每个 fixture × 每个适用 task_type 计算四个指标：

1. 输入 token 压缩率：`1 - compressed_chars / original_chars`
2. 关键字段保留率：critical_paths 标注值与原值完全一致，目标 100%
3. retrieve_original 调用率：目标 <15%
4. 端到端净值：`compressed_chars + retrieve_chars` vs `original_chars`
