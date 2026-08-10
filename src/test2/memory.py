"""对话记忆工具：token 估算、动态摘要、旧消息裁剪和长期记忆提取。"""

import json

from langchain_core.messages import RemoveMessage, SystemMessage, trim_messages
from langchain_core.messages.utils import count_tokens_approximately
from langgraph.config import get_config

from test2.memory_contracts import CATEGORIES, MEMORY_SCOPE, MemoryStore

SUMMARY_TOKEN_THRESHOLD = 6000

SUMMARY_PROMPT = (
    "请把下面的对话内容压缩成简洁的中文摘要。"
    "保留用户偏好、关键事实、已完成事项、未完成事项和重要实体。"
    "不要输出与摘要无关的内容。"
)

EXTRACT_PROMPT = (
    "请从下面的对话中提取值得长期记住的信息，只输出 JSON，不要输出其他内容。"
    "JSON 格式如下：\n"
    "{\n"
    '  "user_preferences": [],\n'
    '  "project_facts": [],\n'
    '  "entities": [],\n'
    '  "key_decisions": [],\n'
    '  "unfinished_tasks": []\n'
    "}\n"
    "每个数组只放简洁、独立、可复用的中文事实。"
)


def parse_memory_json(content: str) -> dict:
    """解析 LLM 返回的长期记忆 JSON。"""
    text = str(content).strip()
    if text.startswith("```"):
        lines = [
            line
            for line in text.splitlines()
            if not line.startswith("```")
        ]
        text = "\n".join(lines).strip()
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("memory extraction must return a JSON object")
    return data


def extract_memory_facts(state, llm, memory_store: MemoryStore) -> dict:
    """最终回复后提取项目级长期记忆；失败时保留旧记忆，不中断对话。"""
    try:
        config = get_config()
        thread_id = config.get("configurable", {}).get("thread_id")

        prompt = [SystemMessage(content=EXTRACT_PROMPT)]
        if state.get("summary"):
            prompt.append(
                SystemMessage(content=f"会话摘要：\n{state['summary']}")
            )
        prompt.extend(state["messages"])

        response = llm.invoke(prompt)
        data = parse_memory_json(response.content)
        for category in CATEGORIES:
            facts = data.get(category)
            if isinstance(facts, list):
                memory_store.merge_facts(
                    MEMORY_SCOPE,
                    category,
                    facts,
                    source_thread_id=thread_id,
                )
    except Exception as e:
        print(f"[memory] 长期记忆提取失败: {e}")
    return {}


def history_tokens(messages) -> int:
    """估算一段消息历史约占用的 token 数。"""
    return count_tokens_approximately(messages)


def build_long_term_memory_section(memory_store: MemoryStore) -> list:
    """构造项目级长期记忆 SystemMessage 段。"""
    context = memory_store.get_memory_context(MEMORY_SCOPE)
    if not context:
        return []
    return [SystemMessage(content=f"项目长期记忆：\n{context}")]


def build_summary_section(summary: str) -> list:
    """构造会话摘要 SystemMessage 段。"""
    if not summary:
        return []
    return [SystemMessage(content=summary)]


def build_recent_history_section(recent_history: list) -> list:
    """返回最近消息列表，不负责裁剪。"""
    return list(recent_history)


def compose_llm_input(
    memory_store: MemoryStore,
    summary: str,
    recent_history: list,
) -> list:
    """按顺序组装长期记忆、会话摘要、最近消息。"""
    return (
        build_long_term_memory_section(memory_store)
        + build_summary_section(summary)
        + build_recent_history_section(recent_history)
    )


def build_summary(llm, retired_messages, old_summary: str = "") -> str:
    """使用不带工具的原始 LLM 生成或更新摘要。"""
    if not retired_messages:
        return old_summary

    prompt = [SystemMessage(content=SUMMARY_PROMPT)]
    if old_summary:
        prompt.append(SystemMessage(content=f"已有摘要：\n{old_summary}"))
    prompt.extend(retired_messages)

    response = llm.invoke(prompt)
    return str(response.content).strip()


def prepare_conversation(state, llm, recent_window: int) -> tuple[str, list, list]:
    """
    返回 (summary, recent_history, removals)。

    - summary: 当前会话摘要，超过 token 阈值时由 LLM 更新。
    - recent_history: 本次应送入 LLM 的最近消息窗口。
    - removals: 被摘要覆盖后需要从 checkpoint 删除的旧消息。
    """
    messages = state["messages"]
    old_summary = state.get("summary") or ""

    recent_history = trim_messages(
        messages,
        max_tokens=recent_window,
        token_counter=len,
        strategy="last",
        start_on="human",
    )

    if history_tokens(messages) <= SUMMARY_TOKEN_THRESHOLD:
        return old_summary, recent_history, []

    kept_ids = {msg.id for msg in recent_history if msg.id is not None}
    retired_messages = [
        msg
        for msg in messages
        if msg.id is not None and msg.id not in kept_ids
    ]  # retired_messages 是被裁剪掉的旧消息
    if not retired_messages:
        return old_summary, recent_history, []

    summary = build_summary(llm, retired_messages, old_summary)
    removals = [RemoveMessage(id=msg.id) for msg in retired_messages]
    return summary, recent_history, removals
