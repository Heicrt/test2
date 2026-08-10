"""LangGraph 核心文件：ReAct 循环的图定义。"""

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph import StateGraph, END

from test2.config import get_llm
from test2.memory import (
    compose_llm_input,
    extract_memory_facts,
    prepare_conversation,
)
from test2.state import ReActState
from test2.tools import TOOLS, TOOL_MAP


def think_node(state: ReActState, llm_with_tools, llm, memory_store) -> dict:
    """
    Think 节点：AI 分析当前状态，决定下一步行动。

    接收：ReActState、绑定工具的 LLM、原始 LLM、长期记忆 Store
    返回：本轮状态更新 dict
    """
    summary, recent_history, removals = prepare_conversation(
        state,
        llm,
        MEMORY_WINDOW,
    )
    llm_input = compose_llm_input(memory_store, summary, recent_history)

    response = llm_with_tools.invoke(llm_input)

    # 判断 AI 是否请求了工具调用
    has_tool_calls = bool(response.tool_calls)

    return {
        "messages": removals + [response],
        "thought": response.content or "(AI 请求调用工具)",
        "should_act": has_tool_calls,
        "tool_calls": response.tool_calls or [],
        "summary": summary,
    }


def act_node(state: ReActState) -> dict:
    """
    Act 节点：执行所有待处理的工具调用。

    接收：ReActState
    返回：{"messages": [ToolMessage, ...]}
    """
    results = []
    for call in state["tool_calls"]:
        tool_name = call["name"]
        tool_args = call["args"]

        # 查找并执行工具
        tool = TOOL_MAP.get(tool_name)
        if tool is None:
            content = f"错误：未知工具 '{tool_name}'"
        else:
            try:
                content = tool.invoke(tool_args)
            except Exception as e:
                content = f"工具执行失败: {e}"

        # 构造 ToolMessage（LangChain 要求的格式）
        results.append(
            ToolMessage(content=str(content), tool_call_id=call["id"])
        )

    return {"messages": results}


def observe_node(state: ReActState) -> dict:
    """
    Observe 节点：记录本轮观察结果，递增循环计数。

    接收：ReActState
    返回：{"iteration": state["iteration"] + 1}
    """
    return {
        "iteration": state["iteration"] + 1,
    }


def should_continue(state: ReActState) -> str:
    """
    条件判断：AI 请求了工具 → 继续循环，否则结束。

    接收：ReActState
    返回："act" 或 "end"
    """
    if state["should_act"]:
        return "act"
    return "end"


MAX_ITERATIONS = 35
MEMORY_WINDOW = 20


def build_graph(
    provider: str,
    *,
    checkpointer,
    memory_store,
):
    """
    显式组装并编译 ReAct Agent 图。

    接收：
        provider：LLM 提供商
        checkpointer：LangGraph checkpointer
        memory_store：长期记忆 Store
    返回：编译后的 LangGraph app
    """
    llm = get_llm(provider)
    llm_with_tools = llm.bind_tools(TOOLS)

    graph = StateGraph(ReActState)
    graph.add_node(
        "think",
        lambda state: think_node(state, llm_with_tools, llm, memory_store),
    )
    graph.add_node("act", act_node)
    graph.add_node("observe", observe_node)
    graph.add_node(
        "extract",
        lambda state: extract_memory_facts(state, llm, memory_store),
    )

    graph.set_entry_point("think")
    graph.add_conditional_edges(
        "think",
        should_continue,
        {
            "act": "act",
            "end": "extract",
        },
    )
    graph.add_edge("act", "observe")
    graph.add_edge("observe", "think")
    graph.add_edge("extract", END)

    return graph.compile(checkpointer=checkpointer)
