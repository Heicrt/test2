from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage

from test2.agent_session import (
    DEFAULT_THREAD_ID,
    clear_thread,
    collect_messages,
    final_answer,
    initial_state,
    session_config,
    stream_agent_events,
    stream_updates,
)
from test2.graph import FINAL_ANSWER_TAG, MAX_ITERATIONS


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


def test_stream_agent_events_emits_tokens_immediately():
    final_id = "final-1"
    final = AIMessage(content="你好", id=final_id)
    app = FakeApp(
        [
            (
                "messages",
                (AIMessageChunk(content="你", id=final_id), {"langgraph_node": "think", "tags": [FINAL_ANSWER_TAG]}),
            ),
            (
                "messages",
                (AIMessageChunk(content="好", id=final_id), {"langgraph_node": "think", "tags": [FINAL_ANSWER_TAG]}),
            ),
            ("updates", {"think": {"messages": [final], "tool_calls": []}}),
            ("updates", {"extract": None}),
        ]
    )

    events = list(stream_agent_events(app, "你好", "thread-1"))

    assert events == [
        ("token", "你"),
        ("token", "好"),
        ("node", "think", {"messages": [final], "tool_calls": []}),
    ]


def test_stream_agent_events_does_not_duplicate_when_chunk_ids_differ():
    final = AIMessage(content="你好", id="final-msg")
    app = FakeApp(
        [
            (
                "messages",
                (AIMessageChunk(content="你", id="chunk-a"), {"langgraph_node": "think", "tags": [FINAL_ANSWER_TAG]}),
            ),
            (
                "messages",
                (AIMessageChunk(content="好", id="chunk-b"), {"langgraph_node": "think", "tags": [FINAL_ANSWER_TAG]}),
            ),
            ("updates", {"think": {"messages": [final], "tool_calls": []}}),
        ]
    )

    events = list(stream_agent_events(app, "你好", "thread-1"))

    assert events == [
        ("token", "你"),
        ("token", "好"),
        ("node", "think", {"messages": [final], "tool_calls": []}),
    ]


def test_stream_agent_events_ignores_extract_tokens():
    app = FakeApp(
        [
            (
                "messages",
                (
                    AIMessageChunk(content="记忆提取输出", id="extract-1"),
                    {"langgraph_node": "extract"},
                ),
            ),
            ("updates", {"extract": None}),
        ]
    )

    events = list(stream_agent_events(app, "你好", "thread-1"))

    assert events == []


def test_stream_agent_events_streams_tool_call_text_immediately():
    tool_call = {"name": "calculator", "args": {}, "id": "call-1"}
    tool_ai = AIMessage(
        content="",
        tool_calls=[tool_call],
        id="call-1",
    )
    app = FakeApp(
        [
            (
                "messages",
                (AIMessageChunk(content="思考中", id="call-1"), {"langgraph_node": "think", "tags": [FINAL_ANSWER_TAG]}),
            ),
            (
                "updates",
                {"think": {"messages": [tool_ai], "tool_calls": [tool_call]}},
            ),
        ]
    )

    events = list(stream_agent_events(app, "你好", "thread-1"))

    assert events == [
        ("token", "思考中"),
        (
            "node",
            "think",
            {"messages": [tool_ai], "tool_calls": [tool_call]},
        )
    ]


def test_stream_agent_events_falls_back_to_full_content():
    final = AIMessage(content="直接回答", id="final-2")
    app = FakeApp(
        [
            ("updates", {"think": {"messages": [final]}}),
        ]
    )

    events = list(stream_agent_events(app, "你好", "thread-1"))

    assert events == [
        ("node", "think", {"messages": [final]}),
        ("token", "直接回答"),
    ]


def test_stream_agent_events_empty_final_content_returns_placeholder():
    final = AIMessage(content="", id="empty-1")
    app = FakeApp(
        [
            ("updates", {"think": {"messages": [final]}}),
        ]
    )

    events = list(stream_agent_events(app, "你好", "thread-1"))

    assert events == [
        ("node", "think", {"messages": [final]}),
        ("token", "（无最终回复）"),
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
