# test2 多轮对话记忆系统代码级详解（V1/V2/V3）

> 本文档按“接收什么、返回什么、作用是什么、逻辑怎么走”的方式，逐函数解析 `test2` 的记忆系统。
>
> 本文重点解析记忆链路相关函数，不把 Web 渲染、SSE、日志等与记忆无关的辅助函数全部展开。
>
> 当前记忆系统由三层组成：
>
> - V1：`thread_id` + 最近消息窗口
> - V2：动态摘要压缩
> - V3：SQLite 持久化 + 项目级长期记忆

---

## 1. 记忆系统总览

### 1.1 记忆分两层

```text
会话级记忆：一次会话内保留对话上下文
项目级记忆：跨会话保留用户偏好和项目事实
```

### 1.2 当前文件职责

```text
src/test2/
├── state.py          # ReActState 与 summary reducer
├── memory.py         # token 估算、摘要、消息裁剪
├── memory_contracts.py # MemoryStore Protocol 与长期记忆常量
├── memory_store.py   # 项目级长期记忆 SQLite Store
├── graph.py          # 节点函数、边、显式 build_graph
├── runtime.py        # create_runtime、AgentRuntime、显式组合
├── agent_session.py  # CLI/Web 共享 stream/clear/最终答案
├── __main__.py       # CLI 入口
├── web.py            # FastAPI + SSE 入口
└── static/index.html # 前端 session_id 与清空按钮
```

---

## 2. 核心概念

### 2.1 `MessagesState`

```python
class MessagesState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
```

作用：

```text
提供带 add_messages 的消息历史字段
```

### 2.2 `add_messages`

作用：

```text
合并节点返回的消息变化
```

规则：

```text
新 id → 追加
相同 id → 替换
RemoveMessage → 删除
无 id → 自动分配
```

### 2.3 `thread_id`

作用：

```text
区分不同会话
```

同一 `thread_id` 共享 checkpoint。

### 2.4 `SqliteSaver`

作用：

```text
把 checkpoint 持久化到 SQLite
```

数据文件：

```text
data/memory.db
```

### 2.5 `LongTermMemoryStore`

作用：

```text
保存项目级长期记忆
```

### 2.6 `MemoryStore` Protocol

作用：

```text
定义图面代码需要的长期记忆读写接口，避免 memory.py 直接依赖具体 SQLite Store
```

接口：

```text
get_memory_context(scope)  # 读取可注入 LLM 的长期记忆
merge_facts(...)           # 写入并合并新事实
```

`LongTermMemoryStore` 是它的具体实现；测试和后续其他存储实现只需要满足这两个方法。

---

## 3. 总体架构

```text
CLI / Web
   │
   ▼
agent_session.stream_updates()
   │
   ▼
thread_id / session_id
   │
   ▼
LangGraph graph
   │
   ├── SqliteSaver checkpoint
   │
   ▼
think_node
   │
   ├── prepare_conversation()
   ├── SystemMessage(长期记忆)
   ├── SystemMessage(summary)
   ├── recent_history
   ├── llm_with_tools.invoke()
   │
   ▼
act_node / observe_node
   │
   ▼
extract_node
   │
   └── LongTermMemoryStore
```

---

## 4. `memory.py` 逐函数解析

### 4.1 常量

```python
SUMMARY_TOKEN_THRESHOLD = 6000
```

作用：

```text
历史 token 超过 6000 时触发摘要
```

### 4.2 `history_tokens(messages)`

**接收**

```text
messages：消息列表
```

**返回**

```text
int：估算 token 数
```

**作用**

```text
判断当前历史是否超长
```

**逻辑**

```python
def history_tokens(messages) -> int:
    return count_tokens_approximately(messages)
```

调用链：

```text
prepare_conversation()
  └── history_tokens()
        └── count_tokens_approximately()
```

### 4.3 `build_long_term_memory_section(memory_store)`

**接收**

```text
memory_store：MemoryStore（LongTermMemoryStore 是实现）
```

**返回**

```text
list：SystemMessage 列表；无长期记忆时返回空列表
```

**作用**

```text
构造项目长期记忆 SystemMessage 段
```

**逻辑**

```python
def build_long_term_memory_section(memory_store) -> list:
    context = memory_store.get_memory_context(MEMORY_SCOPE)
    if not context:
        return []
    return [SystemMessage(content=f"项目长期记忆：\n{context}")]
```

### 4.4 `build_summary_section(summary)`

**接收**

```text
summary：会话摘要字符串
```

**返回**

```text
list：SystemMessage 列表；summary 为空时返回空列表
```

**作用**

```text
构造会话摘要 SystemMessage 段
```

**逻辑**

```python
def build_summary_section(summary: str) -> list:
    if not summary:
        return []
    return [SystemMessage(content=summary)]
```

### 4.5 `build_recent_history_section(recent_history)`

**接收**

```text
recent_history：最近消息列表
```

**返回**

```text
list：原样返回的最近消息列表
```

**作用**

```text
只负责提供最近消息段，不负责裁剪
```

**逻辑**

```python
def build_recent_history_section(recent_history: list) -> list:
    return list(recent_history)
```

### 4.6 `compose_llm_input(memory_store, summary, recent_history)`

**接收**

```text
memory_store：MemoryStore（LongTermMemoryStore 是实现）
summary：会话摘要
recent_history：最近消息
```

**返回**

```text
list：最终 LLM 输入
```

**作用**

```text
按顺序组装长期记忆、摘要、最近消息
```

**逻辑**

```python
def compose_llm_input(memory_store, summary: str, recent_history: list) -> list:
    return (
        build_long_term_memory_section(memory_store)
        + build_summary_section(summary)
        + build_recent_history_section(recent_history)
    )
```

最终顺序：

```text
长期记忆段
+ 会话摘要段
+ 最近消息段
```

### 4.7 `build_summary(llm, retired_messages, old_summary="")`

**接收**

```text
llm：原始 LLM，不绑定工具
retired_messages：被裁剪掉的旧消息
old_summary：已有摘要
```

**返回**

```text
str：新的摘要文本
```

**作用**

```text
把旧消息压缩成摘要
```

**逻辑**

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

执行步骤：

```text
1. 没有旧消息 → 直接返回旧摘要
2. 构造 SystemMessage prompt
3. 有旧摘要 → 放入 prompt
4. 追加旧消息
5. 调用 llm.invoke()
6. 取出 content
7. strip() 后返回
```

### 4.8 `prepare_conversation(state, llm, recent_window)`

**接收**

```text
state：ReActState
llm：原始 LLM
recent_window：最近窗口消息条数
```

**返回**

```text
(summary, recent_history, removals)

summary：摘要
recent_history：最近消息
removals：RemoveMessage 列表
```

**作用**

```text
决定本次对话给 LLM 看什么，以及 checkpoint 中删除什么
```

**逻辑**

```python
def prepare_conversation(state, llm, recent_window):
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
    # kept_ids 结果是一个 set，装着"要保留"的消息 id，例如 {"id-a", "id-b", "id-c"}
    retired_messages = [
        msg
        for msg in messages
        if msg.id is not None and msg.id not in kept_ids
    ]
    # retired_messages 结果是一个 list，装着"被淘汰"的消息，例如 [msg-a, msg-b, msg-c]（销毁清单）
    if not retired_messages:
        return old_summary, recent_history, []
        # 没有被淘汰消息 → 不删除

    summary = build_summary(llm, retired_messages, old_summary)
    #将被淘汰的消息retired_messages与现有摘要old_summary通过llm压缩成新摘要summary
    removals = [RemoveMessage(id=msg.id) for msg in retired_messages]
    #removals 是待删除消息的 id 清单
    return summary, recent_history, removals
```

执行步骤：

```text
1. 读取 messages
2. 读取旧 summary
3. trim_messages 计算最近窗口
4. 估算 token
5. 未超阈值 → 不删除
6. 超阈值 → 找出被淘汰旧消息
7. 没有旧消息 → 不删除
8. 生成新摘要
9. 构造 RemoveMessage
10. 返回三元组
```

---

## 5. `memory_store.py` 逐函数解析

`LongTermMemoryStore` 是 `MemoryStore` Protocol 的 SQLite 实现；`MEMORY_SCOPE` 和 `CATEGORIES` 从 `memory_contracts.py` 导入，避免存储实现与图面代码直接耦合。

### 5.1 `__init__(db_path=DEFAULT_DB_PATH)`

**接收**

```text
db_path：SQLite 文件路径
```

**返回**

```text
LongTermMemoryStore 实例
```

**作用**

```text
初始化 SQLite 连接和数据表
```

**逻辑**

```python
def __init__(self, db_path=DEFAULT_DB_PATH):
    self.db_path = Path(db_path)
    self.db_path.parent.mkdir(parents=True, exist_ok=True)
    self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
    self.conn.row_factory = sqlite3.Row
    self._init_schema()
```

### 5.2 `_init_schema()`

**接收**

```text
无
```

**返回**

```text
无
```

**作用**

```text
创建 long_term_memory 表
```

**逻辑**

```python
def _init_schema(self):
    self.conn.execute(
        """
        CREATE TABLE IF NOT EXISTS long_term_memory (
            scope TEXT NOT NULL,
            category TEXT NOT NULL,
            facts_json TEXT NOT NULL,
            source_thread_id TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (scope, category)
        )
        """
    )
    self.conn.commit()
```

### 5.3 `merge_facts(scope, category, facts, source_thread_id=None)`

**接收**

```text
scope：作用域
category：记忆类别
facts：新事实列表
source_thread_id：来源会话
```

**返回**

```text
无
```

**作用**

```text
合并新事实到长期记忆
```

**逻辑**

```python
def merge_facts(
    self,
    scope,
    category,
    facts,
    source_thread_id=None,
):
    if not facts:
        return

    existing = self.get_facts(scope, category)
    merged = list(existing)
    for fact in facts:
        text = str(fact).strip()
        if text and text not in merged:
            merged.append(text)

    merged = merged[-MAX_FACTS_PER_CATEGORY:]
    self.conn.execute(
        """
        INSERT INTO long_term_memory (...)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(scope, category) DO UPDATE SET ...
        """,
        (...),
    )
    self.conn.commit()
```

执行步骤：

```text
1. facts 为空 → 直接返回
2. 读取旧 facts
3. 逐个去重
4. 限制 100 条
5. 写入 SQLite
6. commit
```

### 5.4 `get_facts(scope, category)`

**接收**

```text
scope + category
```

**返回**

```text
list[str]：事实列表
```

**作用**

```text
读取某类长期记忆
```

**逻辑**

```python
def get_facts(self, scope, category):
    row = self.conn.execute(...).fetchone()
    if not row:
        return []
    return _decode_facts_json(row["facts_json"])
```

`_decode_facts_json` 是模块级私有解析函数，`get_facts` 和 `get_memory_context` 共用，统一处理损坏 JSON、非 list 数据和字符串转换。

### 5.5 `get_memory_context(scope=MEMORY_SCOPE)`

**接收**

```text
scope
```

**返回**

```text
str：可注入 LLM 的长期记忆文本
```

**作用**

```text
把长期记忆格式化成 SystemMessage 内容
```

**逻辑**

```python
def get_memory_context(self, scope=MEMORY_SCOPE):
    rows = self.conn.execute(
        "SELECT category, facts_json ... ORDER BY category"
    ).fetchall()
    parts = []
    for row in rows:
        category = row["category"]
        facts = _decode_facts_json(row["facts_json"])
        if facts:
            parts.append(f"{category}:\n- " + "\n- ".join(facts))
    return "\n\n".join(parts)
```

输出示例：

```text
user_preferences:
- 用户叫小明

project_facts:
- 项目使用 Python
```

### 5.6 `clear_scope(scope)`

**接收**

```text
scope
```

**返回**

```text
无
```

**作用**

```text
清空某作用域长期记忆
```

### 5.7 `close()`

**接收**

```text
无
```

**返回**

```text
无
```

**作用**

```text
关闭 SQLite 连接
```

---

## 6. `state.py` / `graph.py` / `runtime.py` / `agent_session.py` / `memory_contracts.py` 逐函数解析

### 6.1 `state.py` · `keep_existing_summary(old_value, new_value)`

**接收**

```text
old_value：旧 summary
new_value：新 summary
```

**返回**

```text
str：最终 summary
```

**作用**

```text
空字符串不覆盖已有摘要
```

**逻辑**

```python
def keep_existing_summary(old_value, new_value):
    return new_value if new_value else old_value
```

### 6.2 `memory.py` · `parse_memory_json(content)`

**接收**

```text
content：LLM 返回内容
```

**返回**

```text
dict：解析后的 JSON
```

**作用**

```text
解析长期记忆提取结果
```

**逻辑**

```python
def parse_memory_json(content):
    text = str(content).strip()
    if text.startswith("```"):
        lines = [
            line
            for line in text.splitlines()
            if not line.startswith("```")
        ]
        text = "\n".join(lines).strip()
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("memory extraction must return a JSON object")
    return data
```

### 6.3 `think_node(state, llm_with_tools, llm, memory_store)`

**接收**

```text
state：ReActState
llm_with_tools：绑定工具的 LLM
llm：原始 LLM
memory_store：MemoryStore（LongTermMemoryStore 是实现）
```

**返回**

```text
dict：本轮状态更新
```

**作用**

```text
决定是否调用工具，并返回本轮消息、摘要、长期记忆上下文
```

**逻辑**

```python
def think_node(state, llm_with_tools, llm, memory_store):
    summary, recent_history, removals = prepare_conversation(
        state,
        llm,
        MEMORY_WINDOW,
    )

    llm_input = compose_llm_input(memory_store, summary, recent_history)

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

执行步骤：

```text
1. prepare_conversation 处理摘要和裁剪
2. compose_llm_input 组装三段输入
3. 调用 llm_with_tools
4. 判断 tool_calls
5. 返回 removals + response
6. 返回 summary
```

### 6.4 `memory.py` · `extract_memory_facts(state, llm, memory_store)`

**接收**

```text
state：ReActState
llm：长期记忆提取专用 LLM（DeepSeek 会关闭 thinking mode）
memory_store：MemoryStore（LongTermMemoryStore 是实现）
```

**返回**

```text
dict：通常是空 dict
```

**作用**

```text
最终回复后提取项目级长期记忆
```

**逻辑**

```python
def extract_memory_facts(state, llm, memory_store):
    thread_id = get_config()["configurable"]["thread_id"]
    prompt = [SystemMessage(content=EXTRACT_PROMPT)]
    if state.get("summary"):
        prompt.append(SystemMessage(content=f"会话摘要：\n{state['summary']}"))
    prompt.extend(state["messages"])

    # 优先强制 Function Calling，返回 save_long_term_memory 参数
    data = _try_function_calling(llm, prompt)
    # 没有工具调用时，追加“必须调用工具”的纠正消息再试一次
    if data is None:
        corrected_prompt = [
            *prompt,
            SystemMessage(content="你必须调用 save_long_term_memory，不要输出普通文本。"),
        ]
        data = _try_function_calling(llm, corrected_prompt)
    # Function Calling 不可用时回退 JSON mode
    if data is None:
        data = _try_json_mode(llm, prompt)
    # 最后回退普通 LLM 调用
    if data is None:
        data = _try_raw_invoke(llm, prompt)

    for category in CATEGORIES:
        facts = data.get(category)
        if isinstance(facts, list):
            memory_store.merge_facts(
                MEMORY_SCOPE,
                category,
                facts,
                source_thread_id=thread_id,
            )
    return {}
```

执行步骤：

```text
1. 获取 thread_id
2. 构造包含 Schema 和正例的提取 prompt
3. 优先 Function Calling，用 Pydantic 强类型校验
4. 失败后追加纠正消息自纠错一次
5. 回退 JSON mode
6. 回退普通 LLM 调用
7. 按 category 写入 Store
8. 全部失败只打印日志
9. 保留旧记忆，不中断对话
```

### 6.5 `act_node(state)`

**接收**

```text
state：ReActState
```

**返回**

```text
dict：{"messages": [ToolMessage, ...]}
```

**作用**

```text
执行 LLM 请求的工具
```

### 6.6 `observe_node(state)`

**接收**

```text
state：ReActState
```

**返回**

```text
dict：{"iteration": state["iteration"] + 1}
```

**作用**

```text
递增循环计数
```

### 6.7 `should_continue(state)`

**接收**

```text
state：ReActState
```

**返回**

```text
"act" 或 "end"
```

**作用**

```text
决定 think 后进入 act 还是 extract
```

### 6.8 `runtime.py` · `AgentRuntime`

**接收**

```text
app、checkpointer、memory_store、db_path、conn
```

**返回**

```text
AgentRuntime 实例
```

**作用**

```text
保存一个 Agent 实例的共享依赖
```

`AgentRuntime.close()` 负责关闭 SQLite 连接。

### 6.9 `runtime.py` · `create_runtime(provider, db_path=None)`

**接收**

```text
provider：LLM 提供商
db_path：SQLite 文件路径
```

**返回**

```text
AgentRuntime
```

**作用**

```text
显式创建 checkpointer、memory store 和编译后的图
```

### 6.10 `graph.py` · `build_graph(provider, *, checkpointer, memory_store)`

**接收**

```text
provider：LLM 提供商
checkpointer：LangGraph checkpointer
memory_store：MemoryStore（LongTermMemoryStore 是实现）
```

**返回**

```text
编译后的 LangGraph app
```

**作用**

```text
组装 StateGraph；不创建数据库，不产生 import 副作用
```

**逻辑**

```python
def build_graph(provider, *, checkpointer, memory_store):
    llm = get_llm(provider)
    llm_with_tools = llm.bind_tools(TOOLS)
    extract_llm = get_memory_extraction_llm(provider)

    graph = StateGraph(ReActState)
    graph.add_node("think", lambda state: think_node(state, llm_with_tools, llm, memory_store))
    graph.add_node("act", act_node)
    graph.add_node("observe", observe_node)
    graph.add_node("extract", lambda state: extract_memory_facts(state, extract_llm, memory_store))

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
```

图流程：

```text
think
  ├── act → observe → think
  └── extract → END
```

### 6.11 `agent_session.py` · `stream_updates(app, user_message, thread_id)`

**接收**

```text
app：编译后的 LangGraph app
user_message：用户输入文本
thread_id：会话 ID
```

**返回**

```text
Iterator[(node_name, update)]：扁平化的节点更新流
```

**作用**

```text
统一构造初始 state 和 config，让 CLI/Web 共享同一套 stream 调用
```

### 6.12 `agent_session.py` · `final_answer(updates)`

**接收**

```text
updates：(node_name, update) 列表或可迭代对象
```

**返回**

```text
str：最后一个非工具调用、非空内容的 AIMessage
```

**作用**

```text
CLI/Web 使用同一种最终回复提取规则
```

### 6.13 `memory_contracts.py` · `MemoryStore`

**接收**

```text
无实例化参数；它是 Protocol，不创建对象
```

**返回**

```text
接口定义，不是可运行实现
```

**作用**

```text
约束 memory.py 只依赖 get_memory_context / merge_facts，不依赖 SQLite 具体类
```

---

## 7. CLI 入口逐函数解析

### 7.1 `main()`

**接收**

```text
无
```

**返回**

```text
无
```

**作用**

```text
CLI 交互入口
```

**记忆相关逻辑**

```python
THREAD_ID = DEFAULT_THREAD_ID

if user_input.lower() in ("clear", "/clear"):
    clear_thread(runtime, THREAD_ID)
    print("\n🧹 已清空当前会话记忆")
    continue

updates = list(stream_updates(app, user_input, THREAD_ID))
all_messages = collect_messages(updates)
final_content = final_answer(updates)
```

调用图：

```python
config = session_config(THREAD_ID)
```

---

## 8. Web 后端逐函数解析

### 8.1 `chat(request)`

**接收**

```text
request：FastAPI Request
```

**返回**

```text
StreamingResponse
```

**作用**

```text
接收用户消息并运行图
```

**记忆相关逻辑**

```python
session_id = str(body.get("session_id") or "default").strip() or "default"

for event in stream_agent_events(get_agent(), user_input, session_id):
    if event[0] == "node":
        _, node_name, update = event
        yield sse("node", render_node_payload(node_name, update))
    elif event[0] == "token":
        _, content = event
        yield sse("token", {"content": content})

yield sse("done", {})
```

### 8.2 `clear_session(request)`

**接收**

```text
request：FastAPI Request
```

**返回**

```text
{"ok": True}
```

**作用**

```text
清空指定 session 的对话 checkpoint
```

```python
@app.post("/api/clear")
async def clear_session(request: Request):
    body = await request.json()
    session_id = str(body.get("session_id") or "default").strip() or "default"
    clear_thread(get_runtime(), session_id)
    return {"ok": True}
```

---

## 9. 前端逐函数解析

### 9.1 `getSessionId()`

**接收**

```text
无
```

**返回**

```text
string：session_id
```

**作用**

```text
生成或读取浏览器会话 ID
```

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

### 9.2 `send()`

**接收**

```text
无
```

**返回**

```text
Promise
```

**作用**

```text
发送消息并携带 session_id
```

```javascript
body: JSON.stringify({ message, session_id: getSessionId() })
```

### 9.3 `clearSession()`

**接收**

```text
无
```

**返回**

```text
Promise
```

**作用**

```text
调用 /api/clear 并清空页面消息
```

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

## 10. 完整数据流

### 10.1 普通多轮

```text
用户 → thread_id → checkpoint
  → prepare_conversation
  → LLM 输入
  → AI 回复
  → checkpoint 保存
```

### 10.2 摘要触发

```text
token > 6000
  → 保留最近窗口
  → 生成 summary
  → RemoveMessage 删旧消息
  → LLM 输入 summary + recent
```

### 10.3 长期记忆提取

```text
think 无工具
  → extract_node
  → 长期记忆提取 LLM（DeepSeek 关闭 thinking）
  → Function Calling → Pydantic 校验
  → 失败自纠错 / JSON mode / 普通调用
  → LongTermMemoryStore
  → END
```

### 10.4 清空会话

```text
delete_thread(thread_id)
  → 删除对话 checkpoint
  → 项目长期记忆保留
```

---

## 11. 边界与失败场景

### 11.1 未超 token 阈值

```text
不删除旧消息
避免摘要前丢失信息
```

### 11.2 消息没有 id

```text
RemoveMessage 无法删除
当前实现跳过
```

### 11.3 摘要失败

```text
当前会抛错
后续建议回退旧摘要
```

### 11.4 长期记忆提取失败

```text
Function Calling、JSON mode、普通调用全部失败
→ 打印失败日志
→ 保留旧记忆
→ 不中断对话
```

### 11.5 SQLite 文件

```text
data/memory.db
已被 .gitignore 忽略
```

---

## 12. 与 Claude Code / Grok Build / Codex 对比

| 项目 | 写入 | 检索 | test2 对应 |
| --- | --- | --- | --- |
| Claude Code | 每轮自动提取 | 全量注入 | `extract_node` |
| Grok Build | Flush + Dream | FTS5 + 向量 | summary + LongTermMemoryStore |
| Codex | 模型自主工具 | 搜索工具 | 尚未实现 |

---

## 13. 后续演进

```text
1. 摘要失败回退
2. 用户维度记忆
3. 记忆检索
4. DST 槽位
5. 向量检索
```

---

## 14. 验证

```text
1. 编译通过
2. token 估算通过
3. summary reducer 通过
4. RemoveMessage 通过
5. SqliteSaver 持久化通过
6. LongTermMemoryStore 合并去重通过
7. extract_node 失败不中断通过
8. pytest 自动化测试通过
```

---

## 15. 总结

`test2` 记忆系统最终形成一条完整链路：

```text
对话 → messages
  → trim_messages
  → summary
  → RemoveMessage
  → SqliteSaver
  → extract_node
  → LongTermMemoryStore
  → 下一轮注入
```

理解这条链路，就理解了当前 V1/V2/V3 的全部核心逻辑。
