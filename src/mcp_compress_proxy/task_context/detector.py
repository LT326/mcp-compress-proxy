"""Task Context Detector：关键词 + 优先级规则推断任务类型（纯规则，禁止调用 LLM）。

优先级：debug > review > explore > unknown。
匹配：不区分大小写的整词匹配（多词短语按短语边界匹配），对同一输入幂等。
"""

from __future__ import annotations

import re
from enum import Enum


class TaskType(str, Enum):
    DEBUG = "debug"
    REVIEW = "review"
    EXPLORE = "explore"
    UNKNOWN = "unknown"


DEBUG_KEYWORDS = ("fix", "debug", "why", "failing", "error", "traceback", "test failure", "broken")
REVIEW_KEYWORDS = ("review", "PR", "diff", "changes", "commit history", "what changed")
EXPLORE_KEYWORDS = ("find", "where is", "list", "show me", "locate", "structure")


def _count_keywords(messages: list[str], keywords: tuple[str, ...]) -> int:
    text = "\n".join(messages)
    return sum(
        1 for kw in keywords if re.search(rf"\b{re.escape(kw)}\b", text, flags=re.IGNORECASE)
    )


def detect(messages: list[str]) -> TaskType:
    """从最近 3—5 轮对话文本推断任务类型。"""
    if _count_keywords(messages, DEBUG_KEYWORDS) >= 2:
        return TaskType.DEBUG
    if _count_keywords(messages, REVIEW_KEYWORDS) >= 2:
        return TaskType.REVIEW
    if _count_keywords(messages, EXPLORE_KEYWORDS) >= 1:
        return TaskType.EXPLORE
    return TaskType.UNKNOWN
