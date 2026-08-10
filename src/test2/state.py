"""LangGraph 状态定义。"""

from typing import Annotated

from langgraph.graph.message import MessagesState


def keep_existing_summary(old_value: str, new_value: str) -> str:
    """空字符串不覆盖已有摘要，避免每次调用初始状态清空 summary。"""
    return new_value if new_value else old_value


class ReActState(MessagesState):
    """ReAct Agent 的状态"""

    # 早期对话的动态摘要
    summary: Annotated[str, keep_existing_summary]
    # 是否需要执行工具
    should_act: bool
    # 当前待执行的工具调用列表
    tool_calls: list
    # 循环计数（防止无限循环）
    iteration: int
