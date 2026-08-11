"""
ReAct Agent 交互式运行入口

用法:
    python -m test2                        # 读取 .env 中的 LLM_PROVIDER
-
"""

import os
import sys

# 环境变量由 config.py 在导入 test2.graph 时从项目根目录统一加载
from test2.agent_session import (
    DEFAULT_THREAD_ID,
    clear_thread,
    collect_messages,
    final_answer,
    stream_updates,
)
from test2.graph import MAX_ITERATIONS
from test2.runtime import create_runtime

THREAD_ID = DEFAULT_THREAD_ID


def print_react_log(messages, iteration):
    """
    将消息列表和循环轮数转换为控制台 ReAct 过程日志。
    打印标题后遍历消息，识别带 tool_calls 的 AIMessage 和 ToolMessage，
    分别输出 Think、Act、Observe 摘要；当前调用处已注释，保留为可选日志函数。

    Args:
        messages: LangChain 消息列表，通常由 collect_messages 汇总。
        iteration: 已完成的工具循环轮数，来自 observe 节点计数。

    Returns:
        None: 无返回值，日志直接写入控制台。
    """
    print(f"\n{'='*50}")
    print(f"  ReAct 循环完成：共 {iteration} 轮")
    print(f"{'='*50}")

    turn = 0
    for msg in messages:
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            turn += 1
            thought = msg.content or "(请求调用工具)"
            print(f"\n  🤔 [Think #{turn}] {thought[:100]}")
            for tc in msg.tool_calls:
                print(f"  ⚡ [Act] 调用 {tc['name']}({tc['args']})")

        elif msg.__class__.__name__ == "ToolMessage":
            content = msg.content[:80] + "..." if len(msg.content) > 80 else msg.content
            print(f"  👁️  [Observe] {content}")


def print_state_changes(updates):
    """
    将扁平化节点更新流转换为控制台状态变化日志。
    逐个输出 node_name 和 update 字段；messages 字段拆成消息类名和内容，
    tool_calls 附工具调用信息，其他字段直接打印。

    Args:
        updates: stream_updates 产出的 (node_name, update) 列表。

    Returns:
        None: 无返回值，日志直接写入控制台。
    """
    for i, (node_name, update) in enumerate(updates, 1):
        print(f"\n─── 第 {i} 步 · {node_name} 节点 ───")
        for key, value in update.items():
            if key == "messages":
                for msg in value:
                    print(f"  + {msg.__class__.__name__}: {str(msg.content)[:250]}")
                    if getattr(msg, "tool_calls", None):
                        for tc in msg.tool_calls:
                            print(f"    └ {tc['name']}({tc['args']})")
            else:
                print(f"  {key}: {value}")


def main():
    """
    启动 ReAct Agent CLI 交互主循环。
    读取 provider/model，创建 runtime，进入 stdin 交互；
    支持 quit/exit/q 退出和 clear//clear 清空会话，
    每次输入运行 stream_updates 并打印最终回复和状态变化；
    初始化或运行异常时输出友好错误。

    Returns:
        None: 无返回值，程序退出由退出命令或 EOF/KeyboardInterrupt 触发。
    """
    # 读取 provider：从 .env 环境变量
    provider = os.getenv("LLM_PROVIDER", "anthropic").strip().lower()

    model = os.getenv("LLM_MODEL", "")

    print(f"⏳ 正在初始化 ReAct Agent (provider: {provider}, model: {model or '自动'})...")

    # 构建运行时
    try:
        runtime = create_runtime(provider)
        app = runtime.app
    except Exception as e:
        print(f"❌ 初始化失败: {e}")
        print("   请检查 .env 文件中的 LLM_PROVIDER / LLM_MODEL / LLM_API_KEY 配置")
        sys.exit(1)

    print(f"✅ ReAct Agent 已就绪")
    print(f"   输入 quit 或 exit 退出")
    print(f"   输入 clear 或 /clear 清空当前会话记忆")
    print(f"   切换供应商：改 .env 中的 LLM_PROVIDER 即可")
    print(f"   最大循环次数: {MAX_ITERATIONS}")
    print()

    # 交互循环
    while True:
        try:
            user_input = input("你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n👋 再见！")
            break

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit", "q"):
            print("👋 再见！")
            break
        if user_input.lower() in ("clear", "/clear"):
            clear_thread(runtime, THREAD_ID)
            print("\n🧹 已清空当前会话记忆")
            continue

        # 运行 Agent（stream 模式：逐步捕获 state 变化）
        try:
            updates = list(stream_updates(app, user_input, THREAD_ID))
        except Exception as e:
            print(f"\n❌ 运行出错: {e}")
            continue

        # 汇总所有消息
        all_messages = collect_messages(updates)

        # 打印最终回复
        final_content = final_answer(updates)
        if final_content and final_content != "（无最终回复）":
            print(f"\n📎 {final_content}")

        # 统计轮数（observe 节点出现次数）
        iterations = sum(1 for node, _ in updates if node == "observe")

        # 打印 ReAct 过程日志
        #print_react_log(all_messages, iterations)

        # 打印每一步 state 变化（数据视角）
        print_state_changes(updates)


if __name__ == "__main__":
    main()
