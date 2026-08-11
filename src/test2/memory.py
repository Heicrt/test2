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
    """
    将 LLM 返回的原始文本（str）解析为长期记忆 JSON 字典（dict）
    自动剥离 markdown 代码块标记 ```，清洗文本后执行 JSON 反序列化；
    校验根结构必须为字典，非字典格式直接抛出异常。
    Args:
        content: LLM 输出的原始文本，可能包含 markdown 代码围栏标记
        
    Returns:
        dict: 解析完成的长期记忆结构化数据
        dict: 解析完成的长期记忆结构化数据

    Raises:
        json.JSONDecodeError: 文本不符合合法 JSON 格式
        ValueError: JSON 根节点不是对象（字典）
    """
    
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
    """
    将当前会话状态和 LLM 提取结果转换为项目级长期记忆的写入操作。
    自动读取当前 config 的 thread_id，构造包含提取指令、会话摘要和最近消息的 prompt，
    调用原始 LLM 获取 JSON，并按 CATEGORIES 逐类调用 merge_facts；
    任何异常只打印日志并返回空 dict，保留旧记忆、不中断对话。

    Args:
        state: 当前 ReActState，提供 summary 和 messages 用于构造提取 prompt。
        llm: 不带工具的原始 LLM，要求返回符合 EXTRACT_PROMPT 的 JSON 文本。
        memory_store: MemoryStore 实现，提供 merge_facts 写入长期记忆。

    Returns:
        dict: 固定返回空 dict；调用方不依赖返回内容，主要副作用是长期记忆写入。
    """
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
    """
    将一段消息列表估算为近似 token 数量。
    调用 langchain_core 的 count_tokens_approximately，结果用于判断是否触发摘要压缩。

    Args:
        messages: 需要估算的消息列表，通常来自 state["messages"]。

    Returns:
        int: 近似 token 数，不是精确 tokenizer 结果。
    """
    return count_tokens_approximately(messages)


def build_long_term_memory_section(memory_store: MemoryStore) -> list:
    """
    将项目级长期记忆文本转换为可插入 LLM 输入的 SystemMessage 列表。
    读取 MEMORY_SCOPE 的 memory context；无记忆时返回空列表，
    有记忆时包装成“项目长期记忆：\n...”的 SystemMessage。

    Args:
        memory_store: MemoryStore 实现，提供 get_memory_context 读取记忆文本。

    Returns:
        list[SystemMessage]: 长度为 0 或 1；无记忆时不生成空段。
    """
    context = memory_store.get_memory_context(MEMORY_SCOPE)
    if not context:
        return []
    return [SystemMessage(content=f"项目长期记忆：\n{context}")]


def build_summary_section(summary: str) -> list:
    """
    将会话摘要字符串转换为可插入 LLM 输入的 SystemMessage 列表。
    summary 为空时不生成消息，避免空摘要段；非空时直接包装为 SystemMessage。

    Args:
        summary: 当前会话摘要字符串，可为空。

    Returns:
        list[SystemMessage]: 长度为 0 或 1。
    """
    if not summary:
        return []
    return [SystemMessage(content=summary)]


def build_recent_history_section(recent_history: list) -> list:
    """
    将最近消息列表转换为独立的新列表，供 LLM 输入使用。
    返回浅拷贝，避免调用方后续操作修改原 recent_history；不负责裁剪或组装其他段。

    Args:
        recent_history: 最近消息列表，通常由 prepare_conversation 返回。

    Returns:
        list: recent_history 的浅拷贝。
    """
    return list(recent_history)


def compose_llm_input(
    memory_store: MemoryStore,
    summary: str,
    recent_history: list,
) -> list:
    """
    将长期记忆、会话摘要和最近消息按顺序组装为最终 LLM 输入消息列表。
    固定顺序为长期记忆 SystemMessage、摘要 SystemMessage、最近消息；
    任一前置段为空时自动省略，只保留有内容的部分。

    Args:
        memory_store: MemoryStore 实现，提供 get_memory_context。
        summary: 当前会话摘要字符串，可为空。
        recent_history: 最近消息列表，由 prepare_conversation 返回。

    Returns:
        list: 可直接传给 llm_with_tools.invoke 的消息列表。
    """
    return (
        build_long_term_memory_section(memory_store)
        + build_summary_section(summary)
        + build_recent_history_section(recent_history)
    )


def build_summary(llm, retired_messages, old_summary: str = "") -> str:
    """
    将待归档历史消息转换为更新后的对话摘要字符串。
    retired_messages 为空时直接返回 old_summary，不调用 LLM；
    否则构造摘要指令、旧摘要和历史消息 prompt，调用原始 LLM
    并将返回 content 去除首尾空白后作为新摘要。

    Args:
        llm: 不带工具的原始 LLM，用于纯文本摘要生成。
        retired_messages: 被裁剪且不再直接送入 LLM 的历史消息列表；
            为空时跳过 LLM 调用。
        old_summary: 上一轮已有的摘要，默认空字符串；非空时作为增量更新基础。

    Returns:
        str: 更新后的摘要文本；没有待归档消息时原样返回旧摘要。
    """
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
    将完整消息历史转换为本次 LLM 输入所需的摘要、最近消息和待删除消息。
    先用 trim_messages 按消息条数保留最近窗口，start_on="human" 防止从 ToolMessage 开始；
    历史 token 未超阈值时保留旧消息、不生成摘要；
    超过阈值时用 build_summary 生成摘要，并为被裁剪旧消息构造 RemoveMessage。

    Args:
        state: 当前 ReActState，提供 messages 和现有 summary。
        llm: 不带工具的原始 LLM，用于生成或更新摘要。
        recent_window: 最近消息窗口上限，按消息条数而非 token 计算。

    Returns:
        tuple[str, list, list]:
            summary 为当前会话摘要；
            recent_history 为本次应送入 LLM 的最近消息；
            removals 为需要从 checkpoint 删除的旧消息 RemoveMessage 列表。
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
