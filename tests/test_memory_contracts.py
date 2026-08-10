from langchain_core.messages import HumanMessage, SystemMessage

from test2.memory import (
    build_long_term_memory_section,
    build_recent_history_section,
    build_summary_section,
    compose_llm_input,
)
from test2.memory_contracts import MEMORY_SCOPE, MemoryStore


class FakeMemoryStore:
    def __init__(self, context=""):
        self.context = context
        self.merged = []

    def get_memory_context(self, scope=MEMORY_SCOPE):
        return self.context

    def merge_facts(
        self,
        scope,
        category,
        facts,
        source_thread_id=None,
    ):
        self.merged.append((scope, category, list(facts), source_thread_id))


def test_fake_store_matches_memory_store_protocol():
    assert isinstance(FakeMemoryStore(), MemoryStore)


def test_long_term_memory_section_empty():
    assert build_long_term_memory_section(FakeMemoryStore()) == []


def test_long_term_memory_section_nonempty():
    messages = build_long_term_memory_section(
        FakeMemoryStore(context="用户偏好：喜欢简洁回答")
    )

    assert len(messages) == 1
    assert isinstance(messages[0], SystemMessage)
    assert "项目长期记忆" in messages[0].content
    assert "用户偏好：喜欢简洁回答" in messages[0].content


def test_summary_section_empty_and_nonempty():
    assert build_summary_section("") == []

    messages = build_summary_section("会话摘要")
    assert len(messages) == 1
    assert messages[0].content == "会话摘要"


def test_recent_history_section_returns_copy():
    history = [HumanMessage(content="hello")]

    result = build_recent_history_section(history)

    assert result == history
    assert result is not history


def test_compose_llm_input_order():
    store = FakeMemoryStore(context="长期记忆")
    history = [HumanMessage(content="最近消息")]

    result = compose_llm_input(store, "会话摘要", history)

    assert len(result) == 3
    assert isinstance(result[0], SystemMessage)
    assert "长期记忆" in result[0].content
    assert result[1].content == "会话摘要"
    assert result[2].content == "最近消息"


def test_compose_llm_input_without_sections_keeps_history():
    history = [HumanMessage(content="hello")]

    result = compose_llm_input(FakeMemoryStore(), "", history)

    assert result == history
