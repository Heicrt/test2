"""
FastAPI + SSE 前端可视化入口（对话交互界面）

复用 `agent_session.stream_updates()` 生成器，
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

# 环境变量由 config.py 在导入 test2.graph 时从项目根目录统一加载
from test2.agent_session import clear_thread, final_answer, stream_updates
from test2.runtime import create_runtime

# 本文件所在目录（static 与此同级）
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")

app = FastAPI(title="test2 ReAct 可视化")

# Agent 懒加载：首次请求时才构建。
# 这样即使 .env 未配置 API Key，页面也能打开，并在请求时返回友好错误而非启动即 500。
_runtime = None


def get_runtime():
    """
    将 provider 配置转换为全局复用的 AgentRuntime 实例。
    首次调用时读取 LLM_PROVIDER 并调用 create_runtime，
    后续复用模块级 _runtime；保证页面打开不触发 LLM 初始化。

    Returns:
        AgentRuntime: 已包含 app、checkpointer 和 memory_store 的运行时。

    Raises:
        ValueError: 首次创建时 provider 缺失、未知或 protocol 不支持时触发。
    """
    global _runtime
    if _runtime is None:
        provider = os.getenv("LLM_PROVIDER", "anthropic").strip().lower()
        _runtime = create_runtime(provider)
    return _runtime


def get_agent():
    """
    将 Web 全局运行时转换为已编译的 LangGraph app。
    复用 get_runtime 的懒加载结果，供 stream_updates 执行对话。

    Returns:
        object: AgentRuntime.app，即已编译的 LangGraph app。
    """
    return get_runtime().app


def sse(event: str, data: dict) -> str:
    """
    将事件名和数据字典打包为一条 SSE 文本帧。
    自动使用 ensure_ascii=False 和 default=str 序列化 data，
    生成 event 行、data 行和末尾空行。

    Args:
        event: SSE 事件名，如 "user"、"node"、"final"、"error"。
        data: 要发送给前端的结构化数据，会被 JSON 序列化。

    Returns:
        str: 可直接写入 StreamingResponse 的 SSE 文本帧。
    """
    return (
        f"event: {event}\n"
        f"data: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"
    )


def render_node_payload(node: str, update: dict) -> dict:
    """
    将单个节点的状态更新转换为前端友好的扁平 JSON 结构。
    自动把 messages 中的 LangChain 对象提取为 type/content/tool_calls
    纯文本结构，并带上 thought、should_act、tool_calls、iteration，
    避免前端依赖 LangChain 对象结构。

    Args:
        node: 节点名称，如 "think"、"act"、"observe"。
        update: stream_updates 产出的单节点状态更新 dict。

    Returns:
        dict: 包含 node、messages、thought、should_act、tool_calls、iteration 的 JSON 对象。
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
    """
    将 static/index.html 内容读取为 FastAPI HTMLResponse。
    直接读取 STATIC_DIR 下的页面文件并返回，页面本身不触发 Agent 初始化。

    Returns:
        HTMLResponse: Web 可视化页面 HTML。
    """
    with open(os.path.join(STATIC_DIR, "index.html"), encoding="utf-8") as f:
        return HTMLResponse(f.read())


@app.post("/api/chat")
async def chat(request: Request):
    """
    将用户聊天请求转换为 SSE 流式响应。
    解析 message 和 session_id，先返回 user 事件，再通过 stream_updates
    逐节点推送 node 事件，最后返回 final 和 done；任何异常转为 error 事件。

    Args:
        request: FastAPI Request，JSON body 包含 message 和可选 session_id。

    Returns:
        StreamingResponse: media_type 为 text/event-stream 的 SSE 响应。
    """
    body = await request.json()
    user_input = body.get("message", "").strip()
    session_id = str(body.get("session_id") or "default").strip() or "default"
    if not user_input:
        return StreamingResponse(
            iter([sse("error", {"message": "消息不能为空"})]),
            media_type="text/event-stream",
        )

    def gen():
        """
        将用户输入和会话 ID 转换为 SSE 事件生成器。
        先发送 user 事件，再逐个发送 node 事件并收集 updates，
        最后发送 final 和 done；任何异常发送 error 事件。

        Returns:
            Iterator[str]: 每次迭代产生一条 SSE 文本帧。
        """
        yield sse("user", {"content": user_input})
        updates = []
        try:
            for node_name, update in stream_updates(
                get_agent(),
                user_input,
                session_id,
            ):
                updates.append((node_name, update))
                yield sse("node", render_node_payload(node_name, update))

            yield sse("final", {"content": final_answer(updates)})
            yield sse("done", {})
        except Exception as e:
            yield sse("error", {"message": str(e)})

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/api/clear")
async def clear_session(request: Request):
    """
    将会话清空请求转换为 checkpointer 删除操作。
    解析 session_id 并调用 clear_thread，成功后返回 ok=True；
    只清空当前会话，不影响项目级长期记忆。

    Args:
        request: FastAPI Request，JSON body 包含可选 session_id。

    Returns:
        dict: 包含 ok=True 的清理结果。
    """
    body = await request.json()
    session_id = str(body.get("session_id") or "default").strip() or "default"
    clear_thread(get_runtime(), session_id)
    return {"ok": True}


# 静态资源（如果存在 static 目录则挂载；index.html 由 / 直接返回）
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
