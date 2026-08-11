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
    将当前 ReAct 状态转换为 AI 下一步决策和本轮状态更新。
    自动调用 prepare_conversation 获取摘要、最近消息和删除列表，
    再按长期记忆、摘要、最近消息顺序组装 LLM 输入；
    调用绑定工具的 LLM，根据 response.tool_calls 决定 should_act，
    并返回新增消息、thought、tool_calls 和 summary。

    Args:
        state: 当前 ReActState，提供 messages、summary 等图状态。
        llm_with_tools: 已绑定 TOOLS 的 LLM，用于普通回复和请求工具。
        llm: 不带工具的原始 LLM，用于 prepare_conversation 生成摘要。
        memory_store: 项目级长期记忆 Store，用于组装 LLM 输入第一段。

    Returns:
        dict: 包含 messages、thought、should_act、tool_calls、summary 的状态更新。
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
    将当前待执行的工具调用转换为工具执行结果消息。
    遍历 state["tool_calls"]，通过 TOOL_MAP 查找并 invoke 工具；
    未知工具或执行异常转换为对应 ToolMessage 内容，不中断整轮循环。

    Args:
        state: 当前 ReActState，提供待执行的 tool_calls 列表。

    Returns:
        dict: 包含工具执行结果 ToolMessage 列表的 messages 更新。
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
    将当前 ReAct 状态转换为递增后的循环计数。
    只返回 iteration 加 1 的状态更新，不操作 messages；
    observe 节点每执行一次表示完成一轮工具循环。

    Args:
        state: 当前 ReActState，提供当前 iteration 值。

    Returns:
        dict: 包含本轮递增后 iteration 的状态更新。
    """
    return {
        "iteration": state["iteration"] + 1,
    }


def should_continue(state: ReActState) -> str:
    """
    根据当前状态判断 ReAct 循环下一步应进入工具执行还是结束。
    读取 should_act；True 返回 "act"，False 返回 "end"，
    返回值作为 conditional_edges 的映射键，不直接返回节点对象。

    Args:
        state: 当前 ReActState，提供 should_act 决策标记。

    Returns:
        str: "act" 表示继续执行工具，或 "end" 表示进入 extract 后结束。
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
    将 provider、checkpointer、长期记忆 Store 组装为编译后的 LangGraph ReAct Agent。
    自动创建原始 LLM 和绑定工具 LLM，注册 think/act/observe/extract 节点及条件边，
    并将 checkpointer 传给 compile；不在 import 阶段创建数据库或图，避免模块导入副作用。

    Args:
        provider: LLM 供应商名称，传给 get_llm 决定模型实现。
        checkpointer: LangGraph checkpointer，用于多轮会话保存和恢复。
        memory_store: 项目级长期记忆 Store，注入 think 和 extract 节点。

    Returns:
        CompiledStateGraph: 已编译并绑定 checkpointer 的 LangGraph app。

    Raises:
        ValueError: provider 缺失、不在 providers.toml 或 protocol 不支持时触发。
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
