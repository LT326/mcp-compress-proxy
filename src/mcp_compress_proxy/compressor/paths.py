"""路径表达式：field / field.subfield / array[*] / array[start:end] 的解析与展开。

具体路径（concrete path）用元组表示，元素为 str（字典键）或 int（数组下标），
例如 ("commits", 0, "message")；格式化后为 "commits[0].message"。
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Iterator

_SEG_RE = re.compile(r"^(\w+)(?:\[(\*|-?\d*:-?\d*|-?\d+)\])?$")


def parse_path(path: str) -> list[tuple[str, Any]]:
    """把声明式路径解析为段列表；每段 = (字段名, None | "*" | slice | int)。"""
    segments: list[tuple[str, Any]] = []
    for part in path.split("."):
        m = _SEG_RE.match(part)
        if not m:
            raise ValueError(f"invalid path segment {part!r} in {path!r}")
        name, bracket = m.group(1), m.group(2)
        if bracket is None:
            segments.append((name, None))
        elif bracket == "*":
            segments.append((name, "*"))
        elif ":" in bracket:
            start_s, end_s = bracket.split(":", 1)
            segments.append(
                (name, slice(int(start_s) if start_s else None, int(end_s) if end_s else None))
            )
        else:
            segments.append((name, int(bracket)))
    return segments


def extract(payload: Any, path: str) -> list[tuple[tuple, Any]]:
    """按声明式路径取值，返回 (具体路径, 值) 列表；路径不存在时为空列表。

    值可以是叶子标量，也可以是整个子树（路径停在中间层时）。
    """
    out: list[tuple[tuple, Any]] = []
    _walk(payload, parse_path(path), [], out)
    return out


def _walk(node: Any, segments: list[tuple[str, Any]], prefix: list, out: list) -> None:
    if not segments:
        out.append((tuple(prefix), node))
        return
    name, index = segments[0]
    rest = segments[1:]
    if not isinstance(node, dict) or name not in node:
        return
    child = node[name]
    if index is None:
        _walk(child, rest, prefix + [name], out)
    elif index == "*":
        if isinstance(child, list):
            for i, item in enumerate(child):
                _walk(item, rest, prefix + [name, i], out)
    elif isinstance(index, slice):
        if isinstance(child, list):
            for i in range(*index.indices(len(child))):
                _walk(child[i], rest, prefix + [name, i], out)
    else:
        if isinstance(child, list) and -len(child) <= index < len(child):
            pos = index if index >= 0 else len(child) + index
            _walk(child[index], rest, prefix + [name, pos], out)


def set_path(container: dict, concrete: tuple, value: Any) -> None:
    """把 value（深拷贝）写入具体路径，沿途自动创建 dict / list（数组空位补 None）。"""
    node: Any = container
    for i, key in enumerate(concrete):
        last = i == len(concrete) - 1
        if isinstance(key, str):
            if last:
                node[key] = deepcopy(value)
                return
            nxt_is_str = isinstance(concrete[i + 1], str)
            if not isinstance(node, dict):
                return
            if key not in node or not isinstance(node[key], (dict, list)):
                node[key] = {} if nxt_is_str else []
            node = node[key]
        else:
            if not isinstance(node, list):
                return
            while len(node) <= key:
                node.append(None)
            if last:
                node[key] = deepcopy(value)
                return
            if node[key] is None or not isinstance(node[key], (dict, list)):
                node[key] = {} if isinstance(concrete[i + 1], str) else []
            node = node[key]


def iter_leaves(node: Any, prefix: tuple = ()) -> Iterator[tuple[tuple, Any]]:
    """枚举所有叶子（标量 / 空 dict / 空 list）的 (具体路径, 值)。"""
    if isinstance(node, dict):
        if not node:
            yield prefix, node
            return
        for k, v in node.items():
            yield from iter_leaves(v, prefix + (k,))
    elif isinstance(node, list):
        if not node:
            yield prefix, node
            return
        for i, v in enumerate(node):
            yield from iter_leaves(v, prefix + (i,))
    else:
        yield prefix, node


def format_path(concrete: tuple) -> str:
    parts: list[str] = []
    for key in concrete:
        if isinstance(key, int):
            parts.append(f"[{key}]")
        elif not parts:
            parts.append(key)
        else:
            parts.append(f".{key}")
    return "".join(parts)


def get_at(node: Any, concrete: tuple) -> tuple[bool, Any]:
    """按具体路径导航；返回 (是否存在, 值)。"""
    cur = node
    for key in concrete:
        if isinstance(key, int):
            if not isinstance(cur, list) or not 0 <= key < len(cur):
                return False, None
            cur = cur[key]
        else:
            if not isinstance(cur, dict) or key not in cur:
                return False, None
            cur = cur[key]
    return True, cur


def remove_path(container: Any, concrete: tuple) -> None:
    """删除具体路径处的值（找不到则静默忽略）。

    数组下标处置为 None 占位而不是 pop，避免移位破坏其他保留项的下标。
    """
    node = container
    for key in concrete[:-1]:
        if isinstance(key, str):
            if not isinstance(node, dict) or key not in node:
                return
            node = node[key]
        else:
            if not isinstance(node, list) or not 0 <= key < len(node):
                return
            node = node[key]
    last = concrete[-1]
    if isinstance(last, str) and isinstance(node, dict):
        node.pop(last, None)
    elif isinstance(last, int) and isinstance(node, list) and 0 <= last < len(node):
        node[last] = None
