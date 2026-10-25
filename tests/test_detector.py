"""阶段二测试 1—3：Task Context Detector。"""

from mcp_compress_proxy.task_context.detector import TaskType, detect


def test_1_debug_keyword_combo_returns_debug():
    messages = ["why is this test failing?", "help me fix the error"]
    assert detect(messages) == TaskType.DEBUG
    # 幂等：同一输入重复调用结果一致
    assert detect(messages) == detect(messages)


def test_2_review_keyword_combo_returns_review():
    messages = ["can you review the diff", "what changed in this PR?"]
    assert detect(messages) == TaskType.REVIEW


def test_3_empty_messages_return_unknown():
    assert detect([]) == TaskType.UNKNOWN
