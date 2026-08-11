"""对话记忆工具：token 估算、动态摘要、旧消息裁剪和长期记忆提取。"""

import json

from langchain_core.messages import RemoveMessage, SystemMessage, trim_messages
from langchain_core.messages.utils import count_tokens_approximately
from langgraph.config import get_config
from pydantic import BaseModel, Field

from test2.memory_contracts import CATEGORIES, MEMORY_SCOPE, MemoryStore

SUMMARY_TOKEN_THRESHOLD = 6000

MEMORY_EXTRACTION_TOOL_NAME = "save_long_term_memory"


class MemoryExtraction(BaseModel):
    """
    长期记忆提取结果的结构化模型。
    字段与 CATEGORIES 保持一致，用于 Function Calling 参数和 JSON 返回的强类型校验。
    """

    user_preferences: list[str] = Field(default_factory=list)
    project_facts: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    key_decisions: list[str] = Field(default_factory=list)
    unfinished_tasks: list[str] = Field(default_factory=list)


MEMORY_EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "user_preferences": {"type": "array", "items": {"type": "string"}},
        "project_facts": {"type": "array", "items": {"type": "string"}},
        "entities": {"type": "array", "items": {"type": "string"}},
        "key_decisions": {"type": "array", "items": {"type": "string"}},
        "unfinished_tasks": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "user_preferences",
        "project_facts",
        "entities",
        "key_decisions",
        "unfinished_tasks",
    ],
    "additionalProperties": False,
}

MEMORY_EXTRACTION_SCHEMA_TEXT = json.dumps(
    MEMORY_EXTRACTION_SCHEMA,
    ensure_ascii=False,
    indent=2,
)

MEMORY_EXTRACTION_TOOL = {
    "type": "function",
    "function": {
        "name": MEMORY_EXTRACTION_TOOL_NAME,
        "description": "把值得长期记住的用户偏好、项目事实、实体、关键决策和未完成任务保存下来",
        "parameters": MEMORY_EXTRACTION_SCHEMA,
    },
}

MEMORY_EXTRACTION_TOOL_CHOICE = {
    "type": "function",
    "function": {"name": MEMORY_EXTRACTION_TOOL_NAME},
}

SUMMARY_PROMPT = (
    "请把下面的对话内容压缩成简洁的中文摘要。"
    "保留用户偏好、关键事实、已完成事项、未完成事项和重要实体。"
    "不要输出与摘要无关的内容。"
)

EXTRACT_EXAMPLES = (
    "例 1：\n"
    "user: 以后叫我小明\n"
    'save_long_term_memory 参数：{"user_preferences": ["用户希望被称为小明"], '
    '"project_facts": [], "entities": ["用户：小明"], '
    '"key_decisions": [], "unfinished_tasks": []}\n\n'
    "例 2：\n"
    "user: 这个项目叫 AI 金融助手，优先做天气查询和金融计算\n"
    'save_long_term_memory 参数：{"user_preferences": [], '
    '"project_facts": ["项目名称是 AI 金融助手"], "entities": [], '
    '"key_decisions": ["优先开发天气查询和金融计算"], "unfinished_tasks": []}\n\n'
    "例 3：\n"
    "user: 明天记得提醒我继续写记忆文档\n"
    'save_long_term_memory 参数：{"user_preferences": [], '
    '"project_facts": [], "entities": [], "key_decisions": [], '
    '"unfinished_tasks": ["明天提醒用户继续写记忆文档"]}\n'
)

EXTRACT_PROMPT = (
    "请从下面的对话中提取值得长期记住的信息，并调用 "
    f"{MEMORY_EXTRACTION_TOOL_NAME} 工具，只把提取结果放到工具参数 JSON 中，不要输出普通文本。\n"
    "不要输出 DSML、tool_calls 标签，不要调用其他工具。\n\n"
    "参数 JSON Schema：\n"
    f"{MEMORY_EXTRACTION_SCHEMA_TEXT}\n\n"
    "正例：\n"
    f"{EXTRACT_EXAMPLES}\n"
    "现在开始提取："
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


_EMPTY_EXTRACTION = object()


def _content_preview(content) -> str:
    return str(content)[:200]


def _validate_content(content):
    if not content or not str(content).strip():
        return _EMPTY_EXTRACTION
    try:
        data = parse_memory_json(content)
        return MemoryExtraction.model_validate(data).model_dump()
    except Exception:
        return None


def _try_function_calling(llm, prompt, last_content=None):
    bind_tools = getattr(llm, "bind_tools", None)
    if bind_tools is None:
        return None
    try:
        response = bind_tools(
            [MEMORY_EXTRACTION_TOOL],
            tool_choice=MEMORY_EXTRACTION_TOOL_CHOICE,
        ).invoke(prompt)
    except Exception:
        return None

    tool_calls = getattr(response, "tool_calls", None) or []
    if not tool_calls:
        if last_content is not None:
            last_content.append(_content_preview(getattr(response, "content", "")))
        return None

    args = tool_calls[0].get("args") if isinstance(tool_calls[0], dict) else None
    if not isinstance(args, dict):
        if last_content is not None:
            last_content.append(_content_preview(args))
        return None

    try:
        return MemoryExtraction.model_validate(args).model_dump()
    except Exception:
        if last_content is not None:
            last_content.append(_content_preview(args))
        return None


def _try_json_mode(llm, prompt, last_content):
    bind = getattr(llm, "bind", None)
    if bind is None:
        return None
    try:
        response = bind(response_format={"type": "json_object"}).invoke(prompt)
    except Exception:
        return None
    content = getattr(response, "content", None)
    if content is not None:
        last_content.append(_content_preview(content))
    return _validate_content(content)


def _try_raw_invoke(llm, prompt, last_content):
    try:
        response = llm.invoke(prompt)
    except Exception:
        return None
    content = getattr(response, "content", None)
    if content is not None:
        last_content.append(_content_preview(content))
    return _validate_content(content)


def extract_memory_facts(state, llm, memory_store: MemoryStore) -> dict:
    """
    将当前会话状态和 LLM 提取结果转换为项目级长期记忆的写入操作。
    自动读取当前 config 的 thread_id，构造包含提取指令、会话摘要和最近消息的 prompt，
    优先使用 Function Calling 返回 save_long_term_memory 参数并做 Pydantic 校验；
    失败后追加纠正消息自纠错一次，再回退 JSON mode 和普通调用。
    空内容直接跳过；任何异常只打印日志并返回空 dict，保留旧记忆、不中断对话。

    Args:
        state: 当前 ReActState，提供 summary 和 messages 用于构造提取 prompt。
        llm: 长期记忆提取专用 LLM，优先支持 Function Calling 和 JSON mode。
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

        last_content = []
        data = _try_function_calling(llm, prompt, last_content)
        if data is _EMPTY_EXTRACTION:
            print("[memory] 长期记忆提取跳过：LLM 返回空内容")
            return {}
        if data is None:
            corrected_prompt = [
                *prompt,
                SystemMessage(
                    content=(
                        f"你刚才没有返回可用的 {MEMORY_EXTRACTION_TOOL_NAME} 参数。"
                        "现在必须调用 save_long_term_memory，"
                        "并把提取结果放到参数中，不要输出普通文本。"
                    )
                ),
            ]
            data = _try_function_calling(
                llm,
                corrected_prompt,
                last_content,
            )
        if data is _EMPTY_EXTRACTION:
            print("[memory] 长期记忆提取跳过：LLM 返回空内容")
            return {}
        if data is None:
            data = _try_json_mode(llm, prompt, last_content)
        if data is _EMPTY_EXTRACTION:
            print("[memory] 长期记忆提取跳过：LLM 返回空内容")
            return {}
        if data is None:
            data = _try_raw_invoke(llm, prompt, last_content)
        if data is _EMPTY_EXTRACTION:
            print("[memory] 长期记忆提取跳过：LLM 返回空内容")
            return {}
        if data is None:
            preview = last_content[-1] if last_content else ""
            print(
                "[memory] 长期记忆提取失败: "
                f"LLM 未返回可解析的长期记忆 JSON; content={preview!r}"
            )
            return {}

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
