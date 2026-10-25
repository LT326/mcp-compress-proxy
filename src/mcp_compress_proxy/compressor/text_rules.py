"""文本字段压缩策略：error_anchor / head_tail / error_only / drop。

所有模式先算“保留行下标集合”，再统一渲染；行间缺口输出
"... [N lines omitted (sw-compressed)] ..." 标记。
always_preserve_error_signals（R5）在模式结果之上并入错误信号保护行。
"""

from __future__ import annotations

import re
from typing import Any

DEFAULT_ERROR_KEYWORDS = ("error", "exception", "traceback")

# R5：无论 task_type 一律保留的错误信号行（行本身 + 其后 5 行）
SIGNAL_LINE_RE = re.compile(r"exit code|returned non-zero|FAILED|ERROR|Traceback", re.IGNORECASE)
SIGNAL_FOLLOW_LINES = 5

OMITTED_MARKER = "... [{n} lines omitted (sw-compressed)] ..."


def _hit_indices(lines: list[str], keywords: tuple[str, ...] | list[str], context: int) -> set[int]:
    pattern = re.compile("|".join(re.escape(k) for k in keywords), re.IGNORECASE)
    keep: set[int] = set()
    for i, line in enumerate(lines):
        if pattern.search(line):
            keep.update(range(max(0, i - context), min(len(lines), i + context + 1)))
    return keep


def protected_indices(lines: list[str]) -> set[int]:
    """R5 保护行下标：错误信号行 + 其后 SIGNAL_FOLLOW_LINES 行。"""
    keep: set[int] = set()
    for i, line in enumerate(lines):
        if SIGNAL_LINE_RE.search(line):
            keep.update(range(i, min(len(lines), i + SIGNAL_FOLLOW_LINES + 1)))
    return keep


def _render(lines: list[str], keep: set[int]) -> str:
    out: list[str] = []
    prev: int | None = None
    for i in sorted(keep):
        if prev is not None and i > prev + 1:
            out.append(OMITTED_MARKER.format(n=i - prev - 1))
        out.append(lines[i])
        prev = i
    if prev is not None and prev < len(lines) - 1:
        out.append(OMITTED_MARKER.format(n=len(lines) - 1 - prev))
    return "\n".join(out)


def _commit_digest_text(lines: list[str], rule: dict[str, Any]) -> str | None:
    """git_log 式文本的块级摘要：每个 Commit 块压成一行
    `Commit: <hash> | <author> | <message首行>`，丢日期与正文。

    不足 2 个块（非日志文本）→ 返回 None，由调用方回退 head_tail。
    """
    block_start = str(rule.get("block_start", "Commit:"))
    starts = [i for i, l in enumerate(lines) if l.startswith(block_start)]
    if len(starts) < 2:
        return None
    rendered: list[str] = []
    for pos, b in enumerate(starts):
        end = starts[pos + 1] if pos + 1 < len(starts) else len(lines)
        commit_line = lines[b].strip()
        author = ""
        message = ""
        for j in range(b + 1, end):
            l = lines[j]
            if l.startswith("Author:"):
                author = l[len("Author:"):].strip()
            elif l.startswith("Message:"):
                message = l[len("Message:"):].strip()
        rendered.append(f"{commit_line} | {author} | {message}".rstrip(" |"))
    return "\n".join(rendered)


def compress_text(rule: dict[str, Any], text: str, always_preserve: bool) -> str | None:
    """按单条 text_rule 压缩文本字段；返回 None 表示整字段丢弃。

    空结果（无任何保留行且无 R5 保护行）也返回 None —— 整字段没有可留信息。
    """
    lines = text.splitlines()
    mode = rule.get("mode", "drop")

    if mode == "commit_digest":
        digest = _commit_digest_text(lines, rule)
        if digest is not None:
            if always_preserve:
                prot = protected_indices(lines)
                if prot:
                    extra = "\n".join(lines[i] for i in sorted(prot))
                    digest = f"{digest}\n{extra}"
            return digest
        fallback = rule.get("fallback") or {}
        head, tail = int(fallback.get("head", 30)), int(fallback.get("tail", 10))
        keep: set[int] = set()
        if len(lines) <= head + tail:
            keep |= set(range(len(lines)))
        else:
            keep |= set(range(head)) | set(range(len(lines) - tail, len(lines)))
        if always_preserve:
            keep |= protected_indices(lines)
        if not keep:
            return None
        return _render(lines, keep)

    keep: set[int] = set()
    if mode == "error_anchor":
        keep |= _hit_indices(lines, rule.get("keywords", DEFAULT_ERROR_KEYWORDS), int(rule.get("context", 3)))
    elif mode == "head_tail":
        head, tail = int(rule.get("head", 20)), int(rule.get("tail", 20))
        if len(lines) <= head + tail:
            keep |= set(range(len(lines)))
        else:
            keep |= set(range(head)) | set(range(len(lines) - tail, len(lines)))
    elif mode == "error_only":
        keep |= _hit_indices(lines, rule.get("keywords", DEFAULT_ERROR_KEYWORDS), 0)
    elif mode == "first_line":
        if lines:
            keep |= {0}
    elif mode == "drop":
        pass
    else:
        raise ValueError(f"unknown text rule mode: {mode!r}")

    if always_preserve:
        keep |= protected_indices(lines)

    if not keep:
        return None
    return _render(lines, keep)
