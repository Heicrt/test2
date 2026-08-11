import json
from types import SimpleNamespace

from langchain_core.messages import HumanMessage, SystemMessage

import test2.memory as memory
from test2.memory import (
    build_long_term_memory_section,
    build_recent_history_section,
    build_summary_section,
    compose_llm_input,
    extract_memory_facts,
    MEMORY_EXTRACTION_TOOL_CHOICE,
    MEMORY_EXTRACTION_TOOL_NAME,
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


def test_extract_memory_facts_skips_empty_response(monkeypatch, capsys):
    monkeypatch.setattr(
        memory,
        "get_config",
        lambda: {"configurable": {"thread_id": "thread-empty"}},
    )
    llm = SimpleNamespace(
        invoke=lambda prompt: SimpleNamespace(content=None)
    )
    store = FakeMemoryStore()

    result = extract_memory_facts(
        {"summary": "", "messages": []},
        llm,
        store,
    )

    assert result == {}
    assert store.merged == []
    assert "空内容" in capsys.readouterr().out


def _tool_response(args):
    return SimpleNamespace(
        content="",
        tool_calls=[
            {
                "name": MEMORY_EXTRACTION_TOOL_NAME,
                "args": args,
                "id": "call-1",
            }
        ],
    )


def _extract_args():
    return {
        "user_preferences": ["用户希望被称为小黑"],
        "project_facts": [],
        "entities": ["用户：heicrt"],
        "key_decisions": [],
        "unfinished_tasks": [],
    }


def test_extract_memory_facts_function_calling_success(monkeypatch):
    monkeypatch.setattr(
        memory,
        "get_config",
        lambda: {"configurable": {"thread_id": "thread-tool"}},
    )

    class FakeFunctionLLM:
        def __init__(self, response):
            self.response = response
            self.tool_choice = None

        def bind_tools(self, tools, tool_choice=None):
            self.tool_choice = tool_choice
            return self

        def invoke(self, prompt):
            return self.response

    llm = FakeFunctionLLM(_tool_response(_extract_args()))
    store = FakeMemoryStore()

    result = extract_memory_facts(
        {"summary": "", "messages": [HumanMessage("以后叫我小黑")]},
        llm,
        store,
    )

    assert result == {}
    assert llm.tool_choice == MEMORY_EXTRACTION_TOOL_CHOICE
    assert (
        "project",
        "user_preferences",
        ["用户希望被称为小黑"],
        "thread-tool",
    ) in store.merged


def test_extract_memory_facts_self_corrects_when_no_tool_call(monkeypatch):
    monkeypatch.setattr(
        memory,
        "get_config",
        lambda: {"configurable": {"thread_id": "thread-correct"}},
    )

    class SelfCorrectingLLM:
        def __init__(self):
            self.responses = [
                SimpleNamespace(content="我忘记调用工具了", tool_calls=[]),
                _tool_response(_extract_args()),
            ]
            self.calls = []

        def bind_tools(self, tools, tool_choice=None):
            return self

        def invoke(self, prompt):
            self.calls.append(prompt)
            return self.responses.pop(0)

    llm = SelfCorrectingLLM()
    store = FakeMemoryStore()

    extract_memory_facts(
        {"summary": "", "messages": [HumanMessage("以后叫我小黑")]},
        llm,
        store,
    )

    assert len(llm.calls) == 2
    assert store.merged


def test_extract_memory_facts_falls_back_to_json_mode(monkeypatch):
    monkeypatch.setattr(
        memory,
        "get_config",
        lambda: {"configurable": {"thread_id": "thread-json"}},
    )

    class JsonFallbackLLM:
        def __init__(self):
            self.response_format = None

        def bind_tools(self, tools, tool_choice=None):
            raise RuntimeError("function calling unavailable")

        def bind(self, response_format=None):
            self.response_format = response_format
            return SimpleNamespace(
                invoke=lambda prompt: SimpleNamespace(
                    content=json.dumps(_extract_args(), ensure_ascii=False)
                )
            )

        def invoke(self, prompt):
            return SimpleNamespace(content="{}")

    llm = JsonFallbackLLM()
    store = FakeMemoryStore()

    extract_memory_facts(
        {"summary": "", "messages": [HumanMessage("以后叫我小黑")]},
        llm,
        store,
    )

    assert llm.response_format == {"type": "json_object"}
    assert store.merged


def test_extract_prompt_contains_schema_and_examples():
    assert "JSON Schema" in memory.EXTRACT_PROMPT
    assert "additionalProperties" in memory.EXTRACT_PROMPT
    assert "例 1" in memory.EXTRACT_PROMPT
    assert "例 2" in memory.EXTRACT_PROMPT
    assert "例 3" in memory.EXTRACT_PROMPT


def test_extract_memory_facts_all_fail_keeps_old_memory(monkeypatch, capsys):
    monkeypatch.setattr(
        memory,
        "get_config",
        lambda: {"configurable": {"thread_id": "thread-fail"}},
    )

    class AlwaysInvalidLLM:
        def bind_tools(self, tools, tool_choice=None):
            raise RuntimeError("function calling unavailable")

        def bind(self, response_format=None):
            raise RuntimeError("json mode unavailable")

        def invoke(self, prompt):
            return SimpleNamespace(content="<||DSML||tool_calls>")

    llm = AlwaysInvalidLLM()
    store = FakeMemoryStore()

    result = extract_memory_facts(
        {"summary": "", "messages": [HumanMessage("以后叫我小黑")]},
        llm,
        store,
    )

    assert result == {}
    assert store.merged == []
    assert "长期记忆提取失败" in capsys.readouterr().out
