"""YAML Profile 加载器：load_profile(server_name) -> Profile。

Profile 对象可按 task_type 返回对应的 retain/drop 规则（TaskProfile）。
路径语法：field / field.subfield / array[*] / array[start:end]（见 compressor.paths）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_PROFILES_DIR = Path(__file__).resolve().parents[3] / "profiles"


@dataclass
class RetainRule:
    path: str
    priority: str = "normal"


@dataclass
class SkipRule:
    """窄意图调用跳过压缩：tool 匹配且 args_le 条件全满足（参数须存在且 ≤ 阈值）。"""

    tool: str
    args_le: dict[str, float] = field(default_factory=dict)


@dataclass
class TaskProfile:
    retain: list[RetainRule] = field(default_factory=list)
    drop: list[str] = field(default_factory=list)
    text_rules: list[dict] = field(default_factory=list)


@dataclass
class Profile:
    server: str
    task_profiles: dict[str, TaskProfile] = field(default_factory=dict)
    ref_strategy: str = "store_full_payload"
    always_preserve_error_signals: bool = False
    skip_rules: list[SkipRule] = field(default_factory=list)

    def for_task(self, task_type: str) -> TaskProfile | None:
        return self.task_profiles.get(task_type)

    def should_skip(self, tool: str | None, arguments: dict | None) -> bool:
        """该调用是否为窄意图（精确查询）→ 原样透传，不压缩不入库。"""
        if not tool:
            return False
        args = arguments or {}
        for rule in self.skip_rules:
            if rule.tool != tool:
                continue
            if all(
                isinstance(args.get(k), (int, float)) and args[k] <= limit
                for k, limit in rule.args_le.items()
            ):
                return True
        return False


def _parse_task_profile(raw: dict) -> TaskProfile:
    retain: list[RetainRule] = []
    for item in raw.get("retain") or []:
        if isinstance(item, str):
            retain.append(RetainRule(path=item))
        else:
            retain.append(RetainRule(path=item["path"], priority=item.get("priority", "normal")))
    return TaskProfile(
        retain=retain,
        drop=list(raw.get("drop") or []),
        text_rules=list(raw.get("text_rules") or []),
    )


def load_profile(server_name: str, profiles_dir: Path | str | None = None) -> Profile:
    directory = Path(
        profiles_dir
        or os.environ.get("MCP_COMPRESS_PROFILES_DIR")
        or DEFAULT_PROFILES_DIR
    )
    path = directory / f"{server_name}.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return Profile(
        server=data.get("server", server_name),
        task_profiles={
            name: _parse_task_profile(raw)
            for name, raw in (data.get("task_profiles") or {}).items()
        },
        ref_strategy=data.get("ref_strategy", "store_full_payload"),
        always_preserve_error_signals=bool(data.get("always_preserve_error_signals", False)),
        skip_rules=[
            SkipRule(tool=r.get("tool", ""), args_le=dict(r.get("args_le") or {}))
            for r in (data.get("skip_compression_if") or [])
        ],
    )
