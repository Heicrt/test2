"""CLI / Web 共用的 Agent 会话辅助函数。"""

from collections.abc import Iterable, Iterator

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from test2.graph import MAX_ITERATIONS

DEFAULT_THREAD_ID = "cli-default"


def initial_state(user_message: str) -> dict:
    """
    将用户输入字符串转换为 ReActState 的初始状态字典。
    自动把用户消息包装成 HumanMessage，并初始化 thought、summary、should_act、
    tool_calls 和 iteration；返回结果作为 app.stream 输入，
    由 add_messages 与后续节点逐步扩展状态。

    Args:
        user_message: 用户本轮输入文本，非空；会被包装成 HumanMessage 放入 messages。

    Returns:
        dict: 可传给 LangGraph app.stream 的初始 ReActState 值。
    """
    return {
        "messages": [HumanMessage(content=user_message)],
        "thought": "",
        "summary": "",
        "should_act": False,
        "tool_calls": [],
        "iteration": 0,
    }


def session_config(
    thread_id: str,
    recursion_limit: int | None = None,
) -> dict:
    """
    将会话 ID 和可选递归上限转换为 LangGraph 调用配置。
    自动把 thread_id 写入 configurable，并在未显式传入 recursion_limit 时
    使用 MAX_ITERATIONS * 6，避免 ReAct 长循环触发默认递归限制。

    Args:
        thread_id: 会话唯一 ID，用于 checkpointer 区分和恢复不同对话。
        recursion_limit: 可选 LangGraph 递归上限；None 时自动使用 MAX_ITERATIONS * 6。

    Returns:
        dict: 包含 recursion_limit 和 configurable.thread_id 的 LangGraph config。
    """
    effective_limit = (
        MAX_ITERATIONS * 6 if recursion_limit is None else recursion_limit
    )
    return {
        "recursion_limit": effective_limit,
        "configurable": {"thread_id": thread_id},
    }


def stream_updates(
    app,
    user_message: str,
    thread_id: str,
    recursion_limit: int | None = None,
) -> Iterator[tuple[str, dict]]:
    """
    将用户输入和会话配置提交给 LangGraph app，产出扁平的节点状态更新流。
    自动构造 initial_state 和 session_config，以 updates 模式逐节点流式返回；
    LangGraph 用 None 表示节点没有状态更新（如 extract 返回空 dict），
    这类空更新会被跳过，保证下游 update 始终是 dict。

    Args:
        app: 已编译的 LangGraph app，必须支持 stream 和 updates 模式。
        user_message: 用户本轮输入文本。
        thread_id: 会话唯一 ID，用于 checkpointer 恢复历史。
        recursion_limit: 可选 LangGraph 递归上限，None 时使用 session_config 默认值。

    Returns:
        Iterator[tuple[str, dict]]: 按执行顺序逐个产出 (node_name, update)。
    """
    for step in app.stream(
        initial_state(user_message),
        config=session_config(thread_id, recursion_limit),
        stream_mode="updates",
    ):
        for node_name, update in step.items():
            # LangGraph 用 None 表示节点没有产生状态更新（如 extract 返回空 dict）
            if update is None:
                continue
            yield node_name, update


def collect_messages(
    updates: Iterable[tuple[str, dict]],
) -> list[BaseMessage]:
    """
    将扁平化节点更新流汇总为所有消息对象列表。
    遍历每个更新中的 messages 字段并按执行顺序合并；
    非消息状态字段不参与返回，结果供 ReAct 日志统计和最终回复提取使用。

    Args:
        updates: stream_updates 产出的 (node_name, update) 可迭代对象；
            update 必须为 dict。

    Returns:
        list[BaseMessage]: 按节点执行顺序合并后的 LangChain 消息列表。
    """
    messages: list[BaseMessage] = []
    for _, update in updates:
        messages.extend(update.get("messages", []))
    return messages


def final_answer(updates: Iterable[tuple[str, dict]]) -> str:
    """
    将扁平化节点更新流解析为最后一个非工具调用的 AI 回复文本。
    按逆序遍历消息，只接受内容非空且没有 tool_calls 的 AIMessage；
    若没有符合条件的消息，返回“（无最终回复）”作为兜底。

    Args:
        updates: stream_updates 产出的 (node_name, update) 可迭代对象。

    Returns:
        str: 最终 AI 文本回复；找不到合适消息时返回“（无最终回复）”。
    """
    for _, update in reversed(list(updates)):
        for msg in reversed(update.get("messages", [])):
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            if (
                isinstance(msg, AIMessage)
                and content.strip()
                and not getattr(msg, "tool_calls", None)
            ):
                return content
    return "（无最终回复）"


def clear_thread(runtime, thread_id: str) -> None:
    """
    将指定会话 ID 从 checkpointer 中删除，清空该 thread 的会话记忆。
    委托 runtime.checkpointer.delete_thread 删除 checkpoints/writes 中
    属于该 thread 的记录；不影响项目级长期记忆。

    Args:
        runtime: 包含 checkpointer 的 AgentRuntime 对象。
        thread_id: 要清空的会话唯一 ID。

    Returns:
        None: 无返回值，清空结果通过后续同一 thread_id 的对话状态体现。
    """
    runtime.checkpointer.delete_thread(thread_id)
