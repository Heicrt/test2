from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from test2.agent_session import (
    DEFAULT_THREAD_ID,
    clear_thread,
    collect_messages,
    final_answer,
    initial_state,
    session_config,
    stream_updates,
)
from test2.graph import MAX_ITERATIONS


class FakeApp:
    def __init__(self, steps):
        self.steps = steps
        self.last_state = None
        self.last_config = None
        self.last_stream_mode = None

    def stream(self, state, config, stream_mode="updates"):
        self.last_state = state
        self.last_config = config
        self.last_stream_mode = stream_mode
        return iter(self.steps)


class FakeCheckpointer:
    def __init__(self):
        self.deleted = []

    def delete_thread(self, thread_id):
        self.deleted.append(thread_id)


class FakeRuntime:
    def __init__(self):
        self.checkpointer = FakeCheckpointer()


def test_initial_state_builds_human_message():
    state = initial_state("你好")

    assert isinstance(state["messages"][0], HumanMessage)
    assert state["messages"][0].content == "你好"
    assert state["thought"] == ""
    assert state["summary"] == ""
    assert state["should_act"] is False
    assert state["tool_calls"] == []
    assert state["iteration"] == 0


def test_session_config_defaults_to_safe_recursion_limit():
    config = session_config("thread-1")

    assert config["configurable"]["thread_id"] == "thread-1"
    assert config["recursion_limit"] == MAX_ITERATIONS * 6


def test_session_config_allows_custom_recursion_limit():
    config = session_config("thread-1", recursion_limit=12)

    assert config["recursion_limit"] == 12


def test_stream_updates_flattens_node_updates():
    app = FakeApp(
        [
            {"think": {"messages": []}},
            {"act": {"iteration": 1}},
        ]
    )

    updates = list(stream_updates(app, "你好", "thread-1"))

    assert updates == [
        ("think", {"messages": []}),
        ("act", {"iteration": 1}),
    ]
    assert app.last_stream_mode == "updates"
    assert app.last_config["configurable"]["thread_id"] == "thread-1"
    assert app.last_state["messages"][0].content == "你好"


def test_stream_updates_skips_none_node_updates():
    app = FakeApp(
        [
            {"think": {"messages": []}},
            {"extract": None},
            {"act": {"iteration": 1}},
        ]
    )

    updates = list(stream_updates(app, "你好", "thread-1"))

    assert updates == [
        ("think", {"messages": []}),
        ("act", {"iteration": 1}),
    ]


def test_clear_thread_delegates_to_checkpointer():
    runtime = FakeRuntime()

    clear_thread(runtime, "thread-1")

    assert runtime.checkpointer.deleted == ["thread-1"]


def test_collect_messages_merges_all_updates():
    ai = AIMessage(content="ai")
    tool = ToolMessage(content="42", tool_call_id="call-1")
    updates = [
        ("think", {"messages": [ai]}),
        ("act", {"messages": [tool]}),
    ]

    assert collect_messages(updates) == [ai, tool]


def test_final_answer_returns_last_non_tool_ai_message():
    tool_ai = AIMessage(
        content="think",
        tool_calls=[{"name": "calculator", "args": {}, "id": "call-1"}],
    )
    tool = ToolMessage(content="42", tool_call_id="call-1")
    final = AIMessage(content="answer")
    updates = [
        ("think", {"messages": [tool_ai]}),
        ("act", {"messages": [tool]}),
        ("think", {"messages": [final]}),
    ]

    assert final_answer(updates) == "answer"


def test_final_answer_skips_empty_ai_message():
    updates = [("think", {"messages": [AIMessage(content="")]})]

    assert final_answer(updates) == "（无最终回复）"
