"""LangGraph 状态定义。"""

from typing import Annotated

from langgraph.graph.message import MessagesState


def keep_existing_summary(old_value: str, new_value: str) -> str:
    """
    将新的摘要值转换为实际应保存的摘要文本，空字符串时保留旧摘要。
    自动判断 new_value 是否为空；为空时返回 old_value，
    非空时返回 new_value，作为 LangGraph reducer 防止初始状态清空已有 summary。

    Args:
        old_value: 当前状态中的旧摘要，可能是空字符串；当 new_value 为空时作为返回结果。
        new_value: 本次节点写入的新摘要；空字符串表示本次不更新摘要。

    Returns:
        str: 最终应写入 ReActState.summary 的摘要文本。
    """
    return new_value if new_value else old_value


class ReActState(MessagesState):
    """
    定义 ReAct Agent 在 LangGraph 图中的全局状态结构。
    继承 MessagesState，使 messages 使用 add_messages 自动追加；
    summary 使用 keep_existing_summary 防止空字符串覆盖，
    should_act、tool_calls、iteration 分别控制工具循环、待执行工具和防死循环计数。
    """

    # 早期对话的动态摘要
    summary: Annotated[str, keep_existing_summary]
    # 是否需要执行工具
    should_act: bool
    # 当前待执行的工具调用列表
    tool_calls: list
    # 循环计数（防止无限循环）
    iteration: int
