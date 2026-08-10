"""
ReAct Agent 交互式运行入口

用法:
    python -m test2                        # 读取 .env 中的 LLM_PROVIDER
-
"""

import os
import sys
from langchain_core.messages import HumanMessage

# 环境变量由 config.py 在导入 test2.graph 时从项目根目录统一加载
from test2.graph import build_graph, MAX_ITERATIONS, get_checkpointer

THREAD_ID = "cli-default"


def print_react_log(messages, iteration):
    """打印 ReAct 过程日志（Think → Act → Observe）"""
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


def print_state_changes(steps):
    """打印每一步 state 变化（stream_mode="updates" 捕获的记录）"""
    for i, step in enumerate(steps, 1):
        for node_name, update in step.items():
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
    # 读取 provider：从 .env 环境变量
    provider = os.getenv("LLM_PROVIDER", "anthropic").strip().lower()

    model = os.getenv("LLM_MODEL", "")

    print(f"⏳ 正在初始化 ReAct Agent (provider: {provider}, model: {model or '自动'})...")

    # 构建图
    try:
        app = build_graph(provider)
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
            get_checkpointer().delete_thread(THREAD_ID)
            print("\n🧹 已清空当前会话记忆")
            continue

        # 运行 Agent（stream 模式：逐步捕获 state 变化）
        try:
            steps = list(app.stream(
                {
                    "messages": [HumanMessage(content=user_input)],
                    "thought": "",
                    "summary": "",
                    "should_act": False,
                    "tool_calls": [],
                    "iteration": 0,
                },
                config={
                    "recursion_limit": MAX_ITERATIONS * 6,
                    "configurable": {"thread_id": THREAD_ID},
                },
                stream_mode="updates",
            ))
        except Exception as e:
            print(f"\n❌ 运行出错: {e}")
            continue

        # 汇总所有消息
        all_messages = []
        for s in steps:              # 遍历每个 step
            for u in s.values():     # 遍历 step 内各节点的更新
                for m in u.get("messages", []):  # 遍历消息
                    all_messages.append(m)

        # 打印最终回复
        final_msg = all_messages[-1]
        if hasattr(final_msg, "content") and final_msg.content:
            print(f"\n📎 {final_msg.content}")

        # 统计轮数（observe 节点出现次数）
        iterations = sum(1 for s in steps if "observe" in s)

        # 打印 ReAct 过程日志
        #print_react_log(all_messages, iterations)

        # 打印每一步 state 变化（数据视角）
        print_state_changes(steps)


if __name__ == "__main__":
    main()
