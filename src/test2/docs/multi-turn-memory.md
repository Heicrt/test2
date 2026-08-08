# V1 多轮对话记忆实现详解

> 本文档对应 `test2` 当前已经落地的 V1 多轮记忆能力：LangGraph `InMemorySaver` + `thread_id` + 最近 20 条消息窗口。

## 1. 目标

让 CLI 和 Web 在同一个“会话”内记住之前的对话内容，并在下一次提问时把这些历史消息作为上下文送给 LLM。同时通过有限窗口避免对话无限变长后 token 持续膨胀。

当前实现的特点：

- 同一 Python 进程内有效。
- 同一 `thread_id` 下会恢复之前的 `messages` 状态。
- 只把最近 `MEMORY_WINDOW = 20` 条消息送入 LLM。
- 超出窗口的旧消息会被 `RemoveMessage` 从 checkpoint 中删除。
- 服务重启后记忆清空，因为使用的是内存型 checkpointer。

## 2. 总体架构

```text
CLI / Web
   │
   ▼
thread_id / session_id
   │
   ▼
LangGraph compiled graph (checkpointer=InMemorySaver)
   │
   ▼
think_node
   │
   ├── trim_messages 保留最近窗口
   ├── llm_with_tools.invoke(history)
   ├── 返回 RemoveMessage + AIMessage
   │
   ▼
act_node / observe_node
   │
   ▼
checkpoint 保存当前 thread 的最新状态
```

## 3. 核心概念

### 3.1 `ReActState` + `MessagesState` + `add_messages`

当前状态定义：

```python
class ReActState(MessagesState):
    should_act: bool
    tool_calls: list
    iteration: int
```

`MessagesState` 自带：

```python
messages: Annotated[list[AnyMessage], add_messages]
```

`add_messages` 是消息列表的 reducer，负责把节点返回的消息合并到历史中。

规则：

- 无重复 `id` 的新消息：追加。
- 相同 `id` 的新消息：替换旧消息。
- `RemoveMessage`：删除指定 `id` 的消息。
- 无 `id` 的消息：自动分配 `id`。

这是整个多轮记忆的基础：**每个节点只需要返回本轮新增或删除的消息，不需要手动维护完整历史列表。**

### 3.2 `InMemorySaver` / `MemorySaver`

`InMemorySaver` 是 LangGraph 提供的内存型 checkpoint saver。

```python
from langgraph.checkpoint.memory import InMemorySaver

_checkpointer = InMemorySaver()
```

作用：

- 按 `thread_id` 保存每个线程的 checkpoint。
- 保存的是节点执行后的状态快照，包括 `messages` 等 channel。
- 同一个 `thread_id` 再次调用图时，LangGraph 会先恢复之前的 checkpoint。

注意：

- `MemorySaver` 是 `InMemorySaver` 的向后兼容别名。
- 它只存在内存中，进程退出后丢失。
- 生产环境通常改用 SQLite / Postgres 等持久化 checkpointer。

### 3.3 `thread_id`

`thread_id` 是 LangGraph checkpoint 的会话标识。

```python
config = {
    "configurable": {
        "thread_id": "user-123"
    }
}
```

同一个 `thread_id` 表示同一个会话；不同的 `thread_id` 之间状态隔离。

本项目中的映射：

- CLI：`thread_id = "cli-default"`
- Web：`thread_id = session_id`
- 前端：`session_id` 保存在 `localStorage`

### 3.4 `trim_messages`

`trim_messages` 是 LangChain 提供的消息裁剪工具，位于 `langchain_core.messages`。

当前用法：

```python
history = trim_messages(
    state["messages"],
    max_tokens=MEMORY_WINDOW,
    token_counter=len,
    strategy="last",
    start_on="human",
)
```

关键参数：

| 参数 | 当前值 | 含义 |
| --- | --- | --- |
| `max_tokens` | `MEMORY_WINDOW` | 最大保留单位数 |
| `token_counter` | `len` | 用消息条数计数，而不是估算 token 数 |
| `strategy` | `"last"` | 保留末尾消息，丢弃开头消息 |
| `start_on` | `"human"` | 裁剪后从 HumanMessage 开始，避免从 ToolMessage 中间开始 |

`token_counter` 可以替换为：

- `"approximate"`：快速近似 token 数。
- LLM 实例：精确调用 `get_num_tokens_from_messages()`。

`trim_messages` 只负责生成“应该保留的历史”，**它本身不会从 LangGraph checkpoint 中删除旧消息**。

### 3.5 `RemoveMessage`

`RemoveMessage` 是 LangChain 提供的删除消息对象。

```python
from langchain_core.messages import RemoveMessage

RemoveMessage(id="需要删除的消息id")
```

它会被 `add_messages` 识别，并从当前 `messages` 历史中删除对应 `id` 的消息。

当前代码中的配合方式：

```python
kept_ids = {msg.id for msg in history if msg.id is not None}

removals = [
    RemoveMessage(id=msg.id)
    for msg in state["messages"]
    if msg.id is not None and msg.id not in kept_ids
]
```

逻辑：

1. 先用 `trim_messages` 算出保留窗口 `history`。
2. 收集 `history` 中的消息 `id`。
3. 对原历史中不在窗口内的消息生成 `RemoveMessage`。
4. 把 `removals + [response]` 一起返回给 LangGraph。

最终效果：

- 窗口外的旧消息被删除。
- 窗口内的消息保留。
- 新的 AI 回复追加到历史。

### 3.6 `get_checkpointer()` 与 `delete_thread()`

`graph.py` 导出：

```python
def get_checkpointer():
    return _checkpointer
```

CLI 和 Web 清空会话时使用：

```python
get_checkpointer().delete_thread(thread_id)
```

`delete_thread()` 会删除该 `thread_id` 下保存的 checkpoint 和 writes。之后再次使用同一个 `thread_id` 时，LangGraph 会当成新会话处理。

## 4. 代码落地

### 4.1 `graph.py`

新增导入：

```python
from langchain_core.messages import RemoveMessage, trim_messages
from langgraph.checkpoint.memory import InMemorySaver
```

新增常量与共享 checkpointer：

```python
MEMORY_WINDOW = 20

_checkpointer = InMemorySaver()


def get_checkpointer():
    return _checkpointer
```

`think_node` 中的核心逻辑：

```python
history = trim_messages(
    state["messages"],
    max_tokens=MEMORY_WINDOW,
    token_counter=len,
    strategy="last",
    start_on="human",
)

response = llm_with_tools.invoke(history)

kept_ids = {msg.id for msg in history if msg.id is not None}
removals = [
    RemoveMessage(id=msg.id)
    for msg in state["messages"]
    if msg.id is not None and msg.id not in kept_ids
]

return {
    "messages": removals + [response],
    "thought": response.content or "(AI 请求调用工具)",
    "should_act": has_tool_calls,
    "tool_calls": response.tool_calls or [],
}
```

`build_graph` 编译时挂载 checkpointer：

```python
app = graph.compile(checkpointer=_checkpointer)
```

### 4.2 `__main__.py`

CLI 使用固定线程：

```python
THREAD_ID = "cli-default"
```

调用图时传入：

```python
config={
    "recursion_limit": MAX_ITERATIONS * 3,
    "configurable": {"thread_id": THREAD_ID},
}
```

新增清空命令：

```python
if user_input.lower() in ("clear", "/clear"):
    get_checkpointer().delete_thread(THREAD_ID)
    print("\n🧹 已清空当前会话记忆")
    continue
```

### 4.3 `web.py`

Web 请求体新增 `session_id`：

```python
session_id = str(body.get("session_id") or "default").strip() or "default"
```

图调用时把它映射为 `thread_id`：

```python
config={
    "recursion_limit": MAX_ITERATIONS * 3,
    "configurable": {"thread_id": session_id},
}
```

新增清空接口：

```python
@app.post("/api/clear")
async def clear_session(request: Request):
    body = await request.json()
    session_id = str(body.get("session_id") or "default").strip() or "default"
    get_checkpointer().delete_thread(session_id)
    return {"ok": True}
```

### 4.4 `static/index.html`

前端生成并保存会话 ID：

```javascript
function getSessionId() {
  let id = localStorage.getItem("test2_session_id");
  if (!id) {
    id = (crypto.randomUUID && crypto.randomUUID()) ||
      "session-" + Date.now() + "-" + Math.random().toString(36).slice(2);
    localStorage.setItem("test2_session_id", id);
  }
  return id;
}
```

发送消息时携带：

```javascript
body: JSON.stringify({ message, session_id: getSessionId() })
```

清空按钮：

```javascript
await fetch("/api/clear", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ session_id: getSessionId() }),
});

messagesEl.innerHTML = "";
statusEl.textContent = "已清空";
```

## 5. 一次多轮对话的数据流

第一次提问：

```text
用户输入: 我叫小明
   │
   ▼
web: session_id = "abc"
   │
   ▼
graph: thread_id = "abc"
   │
   ▼
think_node
   ├── trim_messages 保留当前窗口
   ├── LLM 回复
   └── checkpoint 保存 [HumanMessage("我叫小明"), AIMessage("你好，小明")]
```

第二次提问：

```text
用户输入: 我叫什么名字？
   │
   ▼
web: session_id = "abc"
   │
   ▼
graph: 先恢复 thread_id = "abc" 的历史
   │
   ▼
new state.messages =
  [HumanMessage("我叫小明"), AIMessage("你好，小明"), HumanMessage("我叫什么名字？")]
   │
   ▼
think_node 送入 LLM
```

如果历史超过 `MEMORY_WINDOW`：

```text
state.messages 有 30 条
   │
   ▼
trim_messages 只保留最近 20 条
   │
   ▼
对最早 10 条生成 RemoveMessage
   │
   ▼
返回 removals + 新 AIMessage
   │
   ▼
checkpoint 中的 messages 被裁剪到 20 条左右
```

## 6. 关键边界

- `trim_messages` 不会自动删除 checkpoint 中的消息，必须配合 `RemoveMessage`。
- 如果消息没有 `id`，无法生成可靠的 `RemoveMessage`；当前实现会跳过 `id is None` 的消息。
- `start_on="human"` 是为了避免把 `ToolMessage` 作为裁剪后的第一条消息，防止模型收到不完整的工具调用对。
- `InMemorySaver` 不是持久化存储，只适合当前 demo。
- 清空会话不会删除前端的 `session_id`，只是删除后端该 `thread_id` 的 checkpoint。
- Web 刷新页面后不会重新渲染历史聊天记录，但 LLM 上下文仍然存在。

## 7. 后续扩展方向

- 使用 SQLite / Postgres checkpointer，让记忆跨进程、跨重启保留。
- 使用 LangGraph Store 保存用户级长期偏好和实体。
- 在 `observe_node` 或独立节点中加入动态摘要，压缩更早的历史。
- 增加 DST 槽位状态，显式跟踪时间、地点、用户意图等业务字段。
- 增加任务状态机，支持工具失败后的恢复和确认流程。

## 8. 验证方式

当前实现已通过以下无 LLM 验证：

```text
1. py_compile 编译通过
2. trim_messages 最近窗口裁剪通过
3. RemoveMessage 删除旧消息通过
4. InMemorySaver 同一 thread_id 保留历史通过
5. delete_thread 后同一 thread_id 从新会话开始通过
```

如需完整功能验证：

- CLI：连续提问两次，确认第二次能引用第一次上下文。
- Web：浏览器发送两条消息，确认同一 `session_id` 下上下文连续。
- 清空：CLI 输入 `clear`，Web 点击“清空”，再提问确认历史已重置。
