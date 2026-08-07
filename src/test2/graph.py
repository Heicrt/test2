"""
LangGraph 核心文件：ReAct 循环的图定义

这是整个项目的心脏。用 ~80 行代码画出 ReAct 循环：
    think → (需要行动?) → act → observe → think → ... → END
"""

from langchain_core.messages import (
    HumanMessage,
    AIMessage,
    ToolMessage,
)
from langgraph.graph import StateGraph, END
from langgraph.graph.message import MessagesState

from test2.config import get_llm
from test2.tools import TOOLS, TOOL_MAP


# ============================================================
# 1. State（状态）定义
#    在所有节点之间传递的数据结构
#    MessagesState = 官方消息列表 + add_messages 合并器
#   MessagesState 中只有包含 messages 字段 ，以及Annotated[list[AnyMessage], add_messages]
# ============================================================

class ReActState(MessagesState):
    """ReAct Agent 的状态"""

    # 是否需要执行工具
    should_act: bool
    # 当前待执行的工具调用列表
    tool_calls: list
    # 循环计数（防止无限循环）
    iteration: int

 

# ============================================================
# 2. 节点函数
#    每个节点：读取 state → 计算 → 返回要更新的字段
# ============================================================

def think_node(state: ReActState, llm_with_tools) -> dict:
    """
    Think 节点：AI 分析当前状态，决定下一步行动

    输入: state["messages"]（完整对话历史）
    输出: 更新 thought, should_act, tool_calls, messages
    """
    response = llm_with_tools.invoke(state["messages"])

    # 判断 AI 是否请求了工具调用
    has_tool_calls = bool(response.tool_calls)

    return {
        "messages": [response],  # AI 回复追加到历史
        "thought": response.content or "(AI 请求调用工具)",
        "should_act": has_tool_calls,
        "tool_calls": response.tool_calls or [],
    }


def act_node(state: ReActState) -> dict:
    """
    Act 节点：执行所有待处理的工具调用

    输入: state["tool_calls"]
    输出: 工具执行结果追加到 messages
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
    Observe 节点：记录本轮观察结果，递增循环计数

    这个节点很轻量，主要用于：
    1. 更新 iteration 计数
    2. 为未来扩展留位置（日志、反思等）
    """
    return {
        "iteration": state["iteration"] + 1,
    }


# ============================================================
# 3. 条件边函数
#    决定循环是继续还是结束
# ============================================================

def should_continue(state: ReActState) -> str:
    """
    条件判断：AI 请求了工具 → 继续循环，否则结束

    返回值对应 conditional_edges 的映射键
    """
    if state["should_act"]:
        return "act"
    return "end"
    #这里返回的是节点的名称，不是节点实例


# ============================================================
# 4. 构建图
# ============================================================

MAX_ITERATIONS = 35  # 最大循环次数（防止无限循环）


def build_graph(provider: str = "anthropic"):
    """
    构建并编译 ReAct Agent 图

    Args:
        provider: LLM 提供商，"anthropic" / "openai" / "ollama"

    Returns:
        编译后的 LangGraph app，可直接 .invoke() 调用
    """
    # 初始化 LLM
    llm = get_llm(provider)

    # 绑定工具到 LLM,形成一个新llm实例
    llm_with_tools = llm.bind_tools(TOOLS)

    # 创建状态图
    graph = StateGraph(ReActState)

    # 添加节点（lambda 包装用于注入 llm_with_tools）
    graph.add_node("think", lambda state: think_node(state, llm_with_tools))
    graph.add_node("act", act_node)
    graph.add_node("observe", observe_node)

    # 设置入口
    graph.set_entry_point("think")

    # 添加条件边：think → 需要行动? → act 或 END
    graph.add_conditional_edges(
        "think",
        should_continue,
        {
            "act": "act",
            "end": END,
        },
    )#左边是should_continue返回的字符串，右边是节点名称
    
    # 添加普通边：act → observe → think（形成循环）
    graph.add_edge("act", "observe")
    graph.add_edge("observe", "think")

    # 编译图
    app = graph.compile()
    return app
