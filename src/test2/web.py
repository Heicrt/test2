"""
FastAPI + SSE 前端可视化入口（对话交互界面）

复用 `__main__.py` 里已写好的 `agent.stream(stream_mode="updates")` 生成器，
把每一步 state 变化用 Server-Sent Events (SSE) 逐节点推送给浏览器。

用法:
    pip install fastapi uvicorn
    python -m uvicorn test2.web:app --reload
    打开 http://127.0.0.1:8000

现有 CLI（python -m test2）不受影响，Web 是并行入口。
"""

import json
import os

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import HumanMessage

# 环境变量由 config.py 在导入 test2.graph 时从项目根目录统一加载
from test2.graph import build_graph, MAX_ITERATIONS

# 本文件所在目录（static 与此同级）
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")

app = FastAPI(title="test2 ReAct 可视化")

# Agent 懒加载：首次请求时才构建。
# 这样即使 .env 未配置 API Key，页面也能打开，并在请求时返回友好错误而非启动即 500。
_agent = None


def get_agent():
    global _agent
    if _agent is None:
        provider = os.getenv("LLM_PROVIDER", "anthropic").strip().lower()
        _agent = build_graph(provider)
    return _agent


def sse(event: str, data: dict) -> str:
    """打包一条 SSE 事件"""
    return (
        f"event: {event}\n"
        f"data: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"
    )


def render_node_payload(node: str, update: dict) -> dict:
    """
    把单个节点的 state 更新，整理成前端好渲染的扁平结构。

    - 把 messages 里的消息提取成纯文本列表（避免前端依赖 LangChain 对象结构）
    - 保留 iteration / should_act / tool_calls 供前端展示
    """
    messages = []
    for msg in update.get("messages", []):
        entry = {"type": msg.__class__.__name__, "content": str(msg.content)}
        tool_calls = getattr(msg, "tool_calls", None)
        if tool_calls:
            entry["tool_calls"] = [
                {"name": tc["name"], "args": tc["args"]} for tc in tool_calls
            ]
        messages.append(entry)

    return {
        "node": node,
        "messages": messages,
        "thought": update.get("thought", ""),
        "should_act": update.get("should_act", False),
        "tool_calls": update.get("tool_calls", []),
        "iteration": update.get("iteration", 0),
    }


@app.get("/", response_class=HTMLResponse)
async def index():
    with open(os.path.join(STATIC_DIR, "index.html"), encoding="utf-8") as f:
        return HTMLResponse(f.read())


@app.post("/api/chat")
async def chat(request: Request):
    body = await request.json()
    user_input = body.get("message", "").strip()
    if not user_input:
        return StreamingResponse(
            iter([sse("error", {"message": "消息不能为空"})]),
            media_type="text/event-stream",
        )

    def gen():
        yield sse("user", {"content": user_input})
        all_messages = []
        try:
            steps = get_agent().stream(
                {
                    "messages": [HumanMessage(content=user_input)],
                    "thought": "",
                    "should_act": False,
                    "tool_calls": [],
                    "iteration": 0,
                },
                config={"recursion_limit": MAX_ITERATIONS * 3},
                stream_mode="updates",
            )
            for step in steps:
                for node_name, update in step.items():
                    payload = render_node_payload(node_name, update)
                    all_messages.extend(payload["messages"])
                    yield sse("node", payload)

            # 最终回复：取最后一条非工具调用、有内容的 AI 消息
            final_content = "（无最终回复）"
            for m in reversed(all_messages):
                if m["type"] == "AIMessage" and m["content"].strip():
                    if not m.get("tool_calls"):
                        final_content = m["content"]
                        break
            yield sse("final", {"content": final_content})
            yield sse("done", {})
        except Exception as e:
            yield sse("error", {"message": str(e)})

    return StreamingResponse(gen(), media_type="text/event-stream")


# 静态资源（如果存在 static 目录则挂载；index.html 由 / 直接返回）
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
