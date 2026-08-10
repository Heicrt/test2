"""Shared CLI/Web agent session helpers."""

from collections.abc import Iterable, Iterator

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from test2.graph import MAX_ITERATIONS

DEFAULT_THREAD_ID = "cli-default"


def initial_state(user_message: str) -> dict:
    """Build the initial ReActState values for a new user turn."""
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
    """Build LangGraph config for a thread, with a safe recursion limit."""
    return {
        "recursion_limit": MAX_ITERATIONS * 6
        if recursion_limit is None
        else recursion_limit,
        "configurable": {"thread_id": thread_id},
    }


def stream_updates(
    app,
    user_message: str,
    thread_id: str,
    recursion_limit: int | None = None,
) -> Iterator[tuple[str, dict]]:
    """Stream LangGraph updates as flattened (node_name, update) pairs."""
    for step in app.stream(
        initial_state(user_message),
        config=session_config(thread_id, recursion_limit),
        stream_mode="updates",
    ):
        for node_name, update in step.items():
            yield node_name, update


def collect_messages(
    updates: Iterable[tuple[str, dict]],
) -> list[BaseMessage]:
    """Collect all message updates from flattened session updates."""
    messages: list[BaseMessage] = []
    for _, update in updates:
        messages.extend(update.get("messages", []))
    return messages


def final_answer(updates: Iterable[tuple[str, dict]]) -> str:
    """Return the last non-tool-call AIMessage with non-empty content."""
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
    """Clear the checkpoint history for one thread."""
    runtime.checkpointer.delete_thread(thread_id)
