"""YAML Profile 加载器：load_profile(server_name) -> Profile。

Profile 对象可按 task_type 返回对应的 retain/drop 规则（TaskProfile）。
路径语法：field / field.subfield / array[*] / array[start:end]（见 compressor.paths）。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

def _resolve_default_profiles_dir() -> Path:
    """源码树优先；pip 安装后回退到 <prefix>/share/mcp-compress-proxy/profiles。"""
    repo = Path(__file__).resolve().parents[3] / "profiles"
    if repo.is_dir():
        return repo
    for prefix in (sys.prefix, sys.base_prefix):
        candidate = Path(prefix) / "share" / "mcp-compress-proxy" / "profiles"
        if candidate.is_dir():
            return candidate
    return repo


DEFAULT_PROFILES_DIR = _resolve_default_profiles_dir()


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
    # 数组载荷：工具输出顶层是 JSON/Python 数组时，用该键包成 {"<key>": [...]}
    # 再套 retain 规则；未声明则数组原样透传（保守）。
    array_envelope: str | None = None
    # 某些 server（如 mcp-server-sqlite）输出的是 Python repr（单引号），
    # 声明后代理会先 ast.literal_eval 解析，但 SQLite 里仍存原文（无损）。
    parse_python_literals: bool = False
    # 工具面很大的 server（如 GitHub MCP 有 40+ 工具、输出形状各异）：
    # 声明后只压缩列表里的工具，其余一律透传，避免白名单把未知形状清空。
    compress_only_tools: list[str] = field(default_factory=list)
    # 多形态工具（如 pull_request_read：get=JSON / get_diff=文本 diff）：
    # 声明为 text_only 后跳过 JSON 压缩，只走 output 文本规则。
    text_only_tools: list[str] = field(default_factory=list)

    def for_task(self, task_type: str) -> TaskProfile | None:
        return self.task_profiles.get(task_type)

    def is_text_only(self, tool: str | None) -> bool:
        return bool(tool) and tool in self.text_only_tools

    def should_compress_tool(self, tool: str | None) -> bool:
        """compress_only_tools 未声明时全部可压缩；声明后只放行列出的工具。"""
        if not self.compress_only_tools:
            return True
        return bool(tool) and tool in self.compress_only_tools

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
        array_envelope=data.get("array_envelope"),
        parse_python_literals=bool(data.get("parse_python_literals", False)),
        compress_only_tools=list(data.get("compress_only_tools") or []),
        text_only_tools=list(data.get("text_only_tools") or []),
        skip_rules=[
            SkipRule(tool=r.get("tool", ""), args_le=dict(r.get("args_le") or {}))
            for r in (data.get("skip_compression_if") or [])
        ],
    )