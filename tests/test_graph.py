from langchain_core.messages import AIMessage, HumanMessage

from test2.graph import FINAL_ANSWER_TAG, think_node
from test2.memory_contracts import MEMORY_SCOPE


class FakeLLM:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def invoke(self, prompt, config=None):
        self.calls.append((prompt, config))
        return self.response


class FakeMemoryStore:
    def get_memory_context(self, scope=MEMORY_SCOPE):
        return ""


def test_think_node_tags_final_answer_llm_call():
    llm_with_tools = FakeLLM(AIMessage(content="ok"))
    llm = FakeLLM(AIMessage(content=""))
    state = {
        "messages": [HumanMessage(content="你好")],
        "summary": "",
        "should_act": False,
        "tool_calls": [],
        "iteration": 0,
    }

    result = think_node(
        state,
        llm_with_tools,
        llm,
        FakeMemoryStore(),
    )

    assert llm_with_tools.calls[0][1] == {"tags": [FINAL_ANSWER_TAG]}
    assert result["messages"][-1].content == "ok"
