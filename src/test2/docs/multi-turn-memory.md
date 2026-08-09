# test2 多轮对话记忆系统完整详解（V1 + V2）

> 本文档以当前代码为准，详细解释 `test2` 的多轮对话记忆实现。
>
> 当前版本包含：
>
> - V1：`InMemorySaver` + `thread_id` + 最近消息窗口
> - V2：token 感知的动态摘要压缩

---

## 1. 概述

### 1.1 当前记忆能力

`test2` 当前可以做到：

- CLI 连续提问时，后一次提问能引用前一次对话内容。
- Web 使用同一个 `session_id` 时，后一次提问能引用前一次对话内容。
- 同一个会话通过 `thread_id` 隔离，不同会话互不干扰。
- 历史消息通过 `messages` channel 保存在 LangGraph checkpoint 中。
- 当历史 token 超过 `SUMMARY_TOKEN_THRESHOLD` 时，早期对话会被 LLM 压缩成摘要。
- 被摘要覆盖的旧消息会从 checkpoint 中删除，避免内存无限增长。
- CLI 和 Web 都支持清空当前会话。

### 1.2 一句话原理

```text
会话 = thread_id
历史 = messages + add_messages
存储 = InMemorySaver
控制 = trim_messages + RemoveMessage
压缩 = LLM 生成 summary + SystemMessage
```

### 1.3 涉及文件

```text
test2/
├── src/test2/
│   ├── memory.py        # token 估算、摘要、裁剪逻辑
│   ├── graph.py         # State、think_node、build_graph
│   ├── __main__.py      # CLI 入口
│   ├── web.py           # FastAPI + SSE 入口
│   └── static/
│       └── index.html   # 前端 session_id 与清空按钮
└── README.md
```

---

## 2. 总体架构

```text
CLI / Web
   │
   ▼
thread_id / session_id
   │
   ▼
LangGraph compiled graph
   │
   ├── checkpointer = InMemorySaver
   │
   ▼
think_node
   │
   ├── prepare_conversation()
   │     ├── count_tokens_approximately()
   │     ├── trim_messages()
   │     ├── build_summary()
   │     └── RemoveMessage()
   │
   ├── [SystemMessage(summary), *recent_history]
   │
   ├── llm_with_tools.invoke()
   │
   ▼
act_node / observe_node
   │
   ▼
checkpoint 保存最新状态
```

---

## 3. 状态设计

### 3.1 `ReActState`

当前状态定义：

```python
from typing import Annotated

from langgraph.graph.message import MessagesState


def keep_existing_summary(old_value: str, new_value: str) -> str:
    """空字符串不覆盖已有摘要，避免每次调用初始状态清空 summary。"""
    return new_value if new_value else old_value


class ReActState(MessagesState):
    summary: Annotated[str, keep_existing_summary]
    should_act: bool
    tool_calls: list
    iteration: int
```

字段说明：

| 字段 | 类型 | 作用 |
| --- | --- | --- |
| `messages` | `list[AnyMessage]` | 完整或最近的对话消息历史 |
| `summary` | `str` | 早期对话摘要 |
| `should_act` | `bool` | 当前 think 是否请求了工具 |
| `tool_calls` | `list` | 待执行的工具调用 |
| `iteration` | `int` | 当前循环次数 |

### 3.2 `MessagesState`

`MessagesState` 是 LangGraph 官方预定义状态：

```python
class MessagesState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
```

它只做一件事：提供带 `add_messages` reducer 的 `messages` 字段。

### 3.3 `add_messages` 合并规则

`add_messages` 是消息列表 reducer。节点不需要返回完整历史，只需要返回本轮变化。

规则：

```text
1. 新消息 id 不存在 → 追加
2. 新消息 id 已存在 → 替换
3. RemoveMessage(id=...) → 删除对应消息
4. 消息没有 id → add_messages 自动分配 id
```

示例：

```python
from langgraph.graph.message import add_messages

old = [HumanMessage(content="你好", id="1")]
new = [HumanMessage(content="修改后的你好", id="1")]

add_messages(old, new)
# → [HumanMessage(content="修改后的你好", id="1")]
```

### 3.4 `summary` reducer

`summary` 不能使用普通覆盖语义，因为 CLI/Web 每次调用都会传入：

```python
{
    "messages": [HumanMessage(content=user_input)],
    "thought": "",
    "summary": "",
    "should_act": False,
    "tool_calls": [],
    "iteration": 0,
}
```

如果 `summary` 是普通字段，每次传入空字符串都会把已有摘要覆盖为空。

所以使用：

```python
def keep_existing_summary(old_value: str, new_value: str) -> str:
    return new_value if new_value else old_value
```

效果：

```text
已有 summary = "用户叫小明"
本次传入 summary = ""
最终 summary = "用户叫小明"
```

---

## 4. 核心框架能力

### 4.1 `InMemorySaver`

`InMemorySaver` 是 LangGraph 提供的内存型 checkpoint saver：

```python
from langgraph.checkpoint.memory import InMemorySaver

_checkpointer = InMemorySaver()
```

作用：

- 按 `thread_id` 保存状态快照。
- 保存 `messages`、`summary`、`should_act` 等 channel。
- 同一个 `thread_id` 再次执行图时，先恢复历史 checkpoint。

注意：

- `MemorySaver` 是旧别名，新代码推荐 `InMemorySaver`。
- 数据只存在于内存。
- 服务重启后丢失。
- 生产环境可替换为 SQLite / Postgres saver。

### 4.2 `thread_id`

`thread_id` 是 LangGraph checkpoint 的会话 ID：

```python
config = {
    "configurable": {
        "thread_id": "user-123"
    }
}
```

本项目映射：

| 入口 | 会话 ID |
| --- | --- |
| CLI | `cli-default` |
| Web | `session_id` |
| 前端 | `localStorage.test2_session_id` |

### 4.3 `trim_messages`

`trim_messages` 用来计算“这次应该给 LLM 看哪些消息”：

```python
from langchain_core.messages import trim_messages

recent_history = trim_messages(
    messages,
    max_tokens=20,
    token_counter=len,
    strategy="last",
    start_on="human",
)
```

参数含义：

| 参数 | 当前值 | 作用 |
| --- | --- | --- |
| `max_tokens` | `20` | 保留数量上限 |
| `token_counter` | `len` | 按消息条数计数 |
| `strategy` | `"last"` | 保留末尾消息 |
| `start_on` | `"human"` | 从 HumanMessage 开始 |

`start_on="human"` 很重要：

- 避免裁剪后第一条是 `ToolMessage`。
- 避免把 `AIMessage(tool_calls)` 和 `ToolMessage` 拆散。

注意：`trim_messages` 只返回新列表，不会修改 checkpoint。

### 4.4 `RemoveMessage`

`RemoveMessage` 是 LangChain 提供的删除消息对象：

```python
from langchain_core.messages import RemoveMessage

RemoveMessage(id="要删除的消息id")
```

它必须配合 `add_messages` 使用：

```python
add_messages(
    old_messages,
    [RemoveMessage(id="要删除的消息id"), AIMessage(content="新回复")],
)
```

作用：

- 真正从 LangGraph state / checkpoint 中删除消息。
- 与 `trim_messages` 配合时，`trim_messages` 负责“算”，`RemoveMessage` 负责“删”。

### 4.5 `SystemMessage`

`SystemMessage` 是系统级消息：

```python
from langchain_core.messages import SystemMessage

SystemMessage(content="用户叫小明，正在讨论天气。")
```

本项目把摘要放在最前面：

```python
llm_input = [SystemMessage(content=summary), *recent_history]
```

这样可以告诉 LLM：

```text
摘要：早期发生了什么
最近消息：最近发生了什么
```

### 4.6 `count_tokens_approximately`

该函数用于估算 token：

```python
from langchain_core.messages.utils import count_tokens_approximately

count_tokens_approximately(messages)
```

特点：

- 快速估算，不调用 LLM。
- 会考虑消息内容、角色、名称。
- 适合在热路径上判断是否需要摘要。

---

## 5. `memory.py` 详解

### 5.1 常量与导入

```python
from langchain_core.messages import RemoveMessage, SystemMessage, trim_messages
from langchain_core.messages.utils import count_tokens_approximately

SUMMARY_TOKEN_THRESHOLD = 6000

SUMMARY_PROMPT = (
    "请把下面的对话内容压缩成简洁的中文摘要。"
    "保留用户偏好、关键事实、已完成事项、未完成事项和重要实体。"
    "不要输出与摘要无关的内容。"
)
```

`SUMMARY_TOKEN_THRESHOLD` 是摘要触发阈值，默认 6000。

### 5.2 `history_tokens()`

```python
def history_tokens(messages) -> int:
    return count_tokens_approximately(messages)
```

用途：

```text
判断当前历史是否超过摘要阈值
```

### 5.3 `build_summary()`

```python
def build_summary(llm, retired_messages, old_summary: str = "") -> str:
    if not retired_messages:
        return old_summary

    prompt = [SystemMessage(content=SUMMARY_PROMPT)]
    if old_summary:
        prompt.append(SystemMessage(content=f"已有摘要：\n{old_summary}"))
    prompt.extend(retired_messages)

    response = llm.invoke(prompt)
    return str(response.content).strip()
```

要点：

- 使用原始 `llm`，不是 `llm_with_tools`。
- 避免摘要过程触发工具调用。
- 如果已有摘要，会把旧摘要和新退休消息一起交给 LLM。
- 返回值是字符串，写入 `summary` 字段。

### 5.4 `prepare_conversation()`

```python
def prepare_conversation(state, llm, recent_window: int) -> tuple[str, list, list]:
    messages = state["messages"]
    old_summary = state.get("summary") or ""

    recent_history = trim_messages(
        messages,
        max_tokens=recent_window,
        token_counter=len,
        strategy="last",
        start_on="human",
    )

    if history_tokens(messages) <= SUMMARY_TOKEN_THRESHOLD:
        return old_summary, recent_history, []

    kept_ids = {msg.id for msg in recent_history if msg.id is not None}
    retired_messages = [
        msg
        for msg in messages
        if msg.id is not None and msg.id not in kept_ids
    ]
    if not retired_messages:
        return old_summary, recent_history, []

    summary = build_summary(llm, retired_messages, old_summary)
    removals = [RemoveMessage(id=msg.id) for msg in retired_messages]
    return summary, recent_history, removals
```

返回结构：

```text
(summary, recent_history, removals)
```

三种情况：

```text
情况 1：token 未超阈值
  summary = 旧摘要
  removals = []

情况 2：token 超阈值，但没有可退休消息
  summary = 旧摘要
  removals = []

情况 3：token 超阈值，有可退休消息
  summary = 新摘要
  removals = [RemoveMessage(...)]
```

为什么只在超阈值时删除：

```text
未超阈值时删除 = 丢失早期上下文
超阈值时删除 = 早期上下文已经变成摘要
```

---

## 6. `graph.py` 详解

### 6.1 关键导入

```python
from typing import Annotated

from langchain_core.messages import (
    AIMessage,
    ToolMessage,
    SystemMessage,
)
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import StateGraph, END
from langgraph.graph.message import MessagesState

from test2.memory import prepare_conversation
```

### 6.2 `think_node`

```python
def think_node(state: ReActState, llm_with_tools, llm) -> dict:
    summary, recent_history, removals = prepare_conversation(
        state,
        llm,
        MEMORY_WINDOW,
    )

    llm_input = recent_history
    if summary:
        llm_input = [SystemMessage(content=summary), *recent_history]

    response = llm_with_tools.invoke(llm_input)

    has_tool_calls = bool(response.tool_calls)

    return {
        "messages": removals + [response],
        "thought": response.content or "(AI 请求调用工具)",
        "should_act": has_tool_calls,
        "tool_calls": response.tool_calls or [],
        "summary": summary,
    }
```

执行顺序：

```text
1. prepare_conversation()
2. 构造 LLM 输入
3. llm_with_tools.invoke()
4. 判断是否有 tool_calls
5. 返回 removals + AI 回复
6. 返回 summary
```

关键点：

- `recent_history` 只控制本次 LLM 输入。
- `removals` 控制 checkpoint 删除。
- `summary` 通过 reducer 持久化。
- `llm_with_tools` 负责正常对话和工具调用。
- `llm` 负责摘要生成。

### 6.3 `build_graph`

```python
def build_graph(provider: str = "anthropic"):
    llm = get_llm(provider)
    llm_with_tools = llm.bind_tools(TOOLS)

    graph = StateGraph(ReActState)

    graph.add_node(
        "think",
        lambda state: think_node(state, llm_with_tools, llm),
    )
    graph.add_node("act", act_node)
    graph.add_node("observe", observe_node)

    graph.set_entry_point("think")

    graph.add_conditional_edges(
        "think",
        should_continue,
        {
            "act": "act",
            "end": END,
        },
    )

    graph.add_edge("act", "observe")
    graph.add_edge("observe", "think")

    app = graph.compile(checkpointer=_checkpointer)
    return app
```

注意：

- `_checkpointer` 是模块级 `InMemorySaver`。
- `llm` 和 `llm_with_tools` 都被传入 `think_node`。
- 图结构没有新增节点，摘要逻辑仍集中在 `think_node`。

---

## 7. CLI 接入

### 7.1 固定会话

```python
THREAD_ID = "cli-default"
```

调用图：

```python
config = {
    "recursion_limit": MAX_ITERATIONS * 3,
    "configurable": {
        "thread_id": THREAD_ID
    },
}
```

### 7.2 清空会话

```python
if user_input.lower() in ("clear", "/clear"):
    get_checkpointer().delete_thread(THREAD_ID)
    print("\n🧹 已清空当前会话记忆")
    continue
```

---

## 8. Web 接入

### 8.1 `session_id`

后端：

```python
session_id = str(body.get("session_id") or "default").strip() or "default"
```

图调用：

```python
config = {
    "recursion_limit": MAX_ITERATIONS * 3,
    "configurable": {
        "thread_id": session_id
    },
}
```

### 8.2 `/api/clear`

```python
@app.post("/api/clear")
async def clear_session(request: Request):
    body = await request.json()
    session_id = str(body.get("session_id") or "default").strip() or "default"
    get_checkpointer().delete_thread(session_id)
    return {"ok": True}
```

### 8.3 前端

生成并保存 session：

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

发送消息：

```javascript
body: JSON.stringify({ message, session_id: getSessionId() })
```

清空：

```javascript
await fetch("/api/clear", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ session_id: getSessionId() }),
});

messagesEl.innerHTML = "";
statusEl.textContent = "已清空";
```

---

## 9. 完整数据流

### 9.1 第一次提问

```text
用户: 我叫小明
   │
   ▼
CLI/Web 传入:
  thread_id = "abc"
  messages = [HumanMessage("我叫小明")]
  summary = ""
   │
   ▼
think_node
  prepare_conversation
    token 未超阈值
    summary = ""
    removals = []
   │
  LLM 输入 = [HumanMessage("我叫小明")]
   │
  LLM 回复 = "你好，小明"
   │
checkpoint:
  messages = [HumanMessage("我叫小明"), AIMessage("你好，小明")]
  summary = ""
```

### 9.2 第二次提问

```text
用户: 我叫什么名字？
   │
   ▼
恢复 thread_id = "abc"
   │
   ▼
state.messages =
  [HumanMessage("我叫小明"), AIMessage("你好，小明"), HumanMessage("我叫什么名字？")]
   │
   ▼
prepare_conversation
  token 未超阈值
  recent_history = 上面 3 条
  removals = []
   │
  LLM 输入 = 上面 3 条
   │
  LLM 回复 = "你叫小明"
```

### 9.3 摘要触发

```text
state.messages token > 6000
   │
   ▼
recent_history = 最近 20 条
   │
   ▼
retired_messages = 20 条之外的历史
   │
   ▼
build_summary(llm, retired_messages, old_summary)
   │
   ▼
summary = "用户叫小明，之前讨论了天气..."
   │
   ▼
removals = [RemoveMessage(...) for 旧消息]
   │
   ▼
LLM 输入 = [SystemMessage(summary), *recent_history]
   │
   ▼
返回 removals + 新 AIMessage
   │
   ▼
checkpoint:
  summary = "用户叫小明，之前讨论了天气..."
  messages = recent_history + 新回复
```

---

## 10. 边界与失败场景

### 10.1 token 未超阈值

行为：

- `trim_messages` 会限制本次 LLM 输入。
- 但旧消息不会从 checkpoint 中删除。
- 后续对话继续增长，直到触发摘要。

原因：

```text
避免在没有摘要时永久丢失早期信息
```

### 10.2 `message.id is None`

`RemoveMessage` 必须依赖消息 id：

```python
if msg.id is not None and msg.id not in kept_ids
```

如果消息没有 id，当前实现会跳过它，不删除。

### 10.3 工具调用被裁剪

风险：

```text
AIMessage(tool_calls) 和 ToolMessage 被拆开
```

缓解：

```text
start_on="human"
```

这会让裁剪后从 HumanMessage 开始，降低拆散工具调用对的风险。

### 10.4 摘要生成失败

当前行为：

- `build_summary` 抛出异常时，`think_node` 会抛出。
- CLI/Web 的异常处理会捕获并返回错误。

后续建议：

```text
摘要失败时回退到旧 summary，而不是中断整次对话
```

### 10.5 内存丢失

`InMemorySaver` 特性：

- 进程内有效。
- 服务重启后所有 thread 丢失。

后续建议：

```text
换 SQLite / Postgres checkpointer
```

### 10.6 默认会话

Web 如果没有传 `session_id`：

```python
session_id = "default"
```

风险：

```text
所有未传 session_id 的客户端共用同一个会话
```

前端已经自动生成 `session_id`，所以正常浏览器不会触发。

### 10.7 清空会话

清空只调用：

```python
get_checkpointer().delete_thread(thread_id)
```

效果：

- 删除该 thread 的 checkpoint。
- 删除该 thread 的 messages。
- 删除该 thread 的 summary。
- 前端 `session_id` 本身不删除。

---

## 11. 后续扩展方向

当前未实现：

- SQLite / Postgres 持久化。
- 用户级长期记忆。
- 向量检索。
- DST 槽位。
- 任务状态机。
- 摘要失败回退。

建议优先级：

```text
1. SQLite checkpointer
2. 摘要失败回退
3. LangGraph Store 用户偏好
4. DST 槽位
5. 向量检索
```

---

## 12. 验证与测试

### 12.1 已通过的验证

```text
1. py_compile 编译通过
2. count_tokens_approximately 估算通过
3. summary reducer 不清空已有摘要通过
4. 未超阈值时不删除旧消息通过
5. 超阈值时生成摘要并返回 RemoveMessage 通过
6. SystemMessage + recent_history 组合通过
7. ReActState + InMemorySaver 保存/恢复 summary 通过
```

### 12.2 手动验证

CLI：

```text
你: 我叫小明
你: 我叫什么名字？
```

预期：

```text
第二次回答“你叫小明”
```

Web：

```text
同一浏览器发送两条消息
```

预期：

```text
第二条能引用第一条上下文
```

清空：

```text
CLI 输入 clear
Web 点击清空
```

预期：

```text
再次提问从空历史开始
```

### 12.3 长对话摘要验证

制造超过阈值的历史，然后观察：

```text
1. summary 字段被更新
2. 早期消息被 RemoveMessage 删除
3. LLM 仍能通过 summary 引用早期关键信息
```

---

## 13. 总结

当前记忆系统是标准的 LangGraph 方案：

```text
MessagesState
  + add_messages
  + InMemorySaver
  + thread_id
  + trim_messages
  + RemoveMessage
  + SystemMessage summary
```

它已经具备：

- 会话隔离。
- 多轮上下文。
- token 阈值判断。
- 动态摘要。
- 内存状态清理。

后续工程化重点是把内存替换为持久化存储，并补上摘要失败回退和用户级长期记忆。
