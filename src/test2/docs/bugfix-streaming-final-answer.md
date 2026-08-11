# test2 Web 流式输出 Bug 修复详解

> 本文记录 Web 入口从“节点更新流”升级到“节点 + 最终回复逐字流”过程中出现的三个连续问题，以及最终修复方案。

## 一、问题背景

test2 的 Web 入口由 `web.py` 提供 FastAPI + SSE，前端 `static/index.html` 负责渲染。后端通过 `agent_session.stream_agent_events()` 同时消费 LangGraph 的两类流：

- `updates`：每个节点跑完后返回的状态增量，用来渲染 Think / Act / Observe 卡片。
- `messages`：LLM 流式输出的 token，用来实现最终回答的逐字显示。

理想的事件顺序是：

```text
user -> node(think) -> node(act) -> node(observe) -> token... -> done
```

## 二、Bug 症状

实际使用中出现过以下现象：

1. Web 页面没有逐字输出，最终回答等到整个节点跑完后一次性出现。
2. 最终回答被重复输出，用户看到两段几乎相同的 AI 回复。
3. 最后一个 `🤔 Think(AI 请求调用工具)` 卡片出现在最终回答文本后面。
4. 长期记忆提取阶段偶尔打印：

```text
[memory] 长期记忆提取失败: Expecting value: line 1 column 1 (char 0)
```

## 三、根因分析

### 3.1 没有真正逐字输出

第一版实现为了让“工具调用阶段的思考文本”不混入最终回复，把 `messages` token 先缓存到 `AIMessageChunk.id`，等 `think` 节点的 `updates` 到达后再 flush。

问题在于：`updates` 只有在节点执行完、LLM 调用结束后才产生。因此前端看到的仍是“一次性完整回答”，不是逐字流。

### 3.2 最终回答重复

真实模型的 `AIMessageChunk.id` 并不一定等于最终 `AIMessage.id`。第一版按 id 判断“已经流式输出过”，导致：

- 先输出了 token；
- 最终节点 update 里找不到匹配 id；
- 又把完整 `content` 作为 fallback 输出一次。

结果就是最终回答出现两遍。

### 3.3 `extract` 节点的 LLM 输出混入最终回答

`messages` 模式会返回图中**所有 LLM 调用**的 token，不只是最终回答。

当前图里：

- `think_node` 调用 `llm_with_tools.invoke()`，生成用户可见回复或工具调用；
- `extract_node` 调用 `llm.invoke()`，做长期记忆提取。

如果不做区分，`extract` 节点里 LLM 输出的一段文本也会被当作最终回答推给前端。这是“最终回答后又出现一段相似文本”的另一个来源。

### 3.4 Think 卡片跑到回答后面

即时 token 流天然先于该 `think` 节点的 `updates` 到达。前端收到 token 后先创建 assistant 气泡，之后才收到该节点的 `node` 事件；旧逻辑统一 `append()`，所以最终 Think 卡片被追加到了回答气泡后面。

## 四、最终修复方案

### 4.1 token 到达即发送

`stream_agent_events()` 不再缓存 token，`AIMessageChunk` 一到就 yield：

```python
("token", chunk.content)
```

这保证 Web 前端能看到真正的逐字输出。

### 4.2 用“当前 think 是否已流式输出”判断 fallback

不再依赖消息 id，而是记录当前 `think` 节点是否已经输出过 token：

```python
tokens_emitted_for_think = False
```

每次 yield 最终回答 token 时置为 `True`。收到 `think` 节点 update 时：

- 如果已经流式输出过，不输出 fallback；
- 如果没有流式输出过，输出完整 content；
- 空内容输出 `（无最终回复）`。

这样即使 chunk id 与最终消息 id 不一致，也不会重复。

### 4.3 用 `FINAL_ANSWER_TAG` 只放行最终回答 token

在 `graph.py` 定义共享常量：

```python
FINAL_ANSWER_TAG = "final-answer"
```

`think_node` 给真正的最终 LLM 调用打 tag：

```python
response = llm_with_tools.invoke(
    llm_input,
    config={"tags": [FINAL_ANSWER_TAG]},
)
```

`agent_session.stream_agent_events()` 只转发带这个 tag 的 `messages` token：

```python
if FINAL_ANSWER_TAG in metadata.get("tags", []):
    tokens_emitted_for_think = True
    yield ("token", chunk.content)
```

这样：

- 最终回答 token 仍然即时输出；
- `extract`、摘要生成等其他 LLM 的 token 不会进入 SSE；
- tag 定义、使用和过滤使用同一个常量，避免魔法字符串。

### 4.4 前端把 node 卡片插入到 assistant 气泡前

`renderNode()` 支持指定插入位置：

```js
function renderNode(data, beforeEl) {
  // ...
  if (beforeEl) {
    messagesEl.insertBefore(div, beforeEl);
  } else {
    append(div);
  }
}
```

`handleEvent` 收到 `node` 时传入 `assistantEl`：

```js
renderNode(data, assistantEl);
```

因此即使后端事件顺序是 `token -> node`，卡片也会被插到回答气泡前面。

## 五、修复后事件流

### 5.1 普通最终回答

```text
user
-> node(think)
-> token(最终回答逐字输出)
-> done
```

### 5.2 工具调用场景

```text
user
-> node(think: 计划调用工具)
-> node(act)
-> node(observe)
-> node(think: 最终回答)
-> token(最终回答逐字输出)
-> done
```

### 5.3 `extract` 节点

`extract` 节点仍会执行长期记忆提取，但它的 LLM token 不会进入 Web SSE，也不会出现在最终回答之后。

## 六、主要改动文件

| 文件 | 改动 |
| --- | --- |
| `src/test2/graph.py` | 新增 `FINAL_ANSWER_TAG`，最终 LLM 调用打 tag |
| `src/test2/agent_session.py` | `stream_agent_events()` 即时输出 token，只放行 `FINAL_ANSWER_TAG` token，并用当前 think 标志避免重复 fallback |
| `src/test2/static/index.html` | `renderNode()` 支持插入到 assistant 气泡前 |
| `src/test2/memory.py` | 空响应时跳过长期记忆提取，避免无意义的 JSON 解析错误 |
| `tests/test_agent_session.py` | 更新 token metadata，新增 extract token 过滤和重复回答回归测试 |
| `tests/test_graph.py` | 锁定 `think_node` 会带 `FINAL_ANSWER_TAG` 调用最终 LLM |

## 七、验证方式

运行：

```bash
uv run pytest
python -m compileall -q src/test2
git diff --check
```

当前验证结果：

- `uv run pytest`：28 passed
- Python 编译检查：通过
- 本次改动文件 `git diff --check`：通过

手动验证：

1. 启动 Web：

```bash
uv run python -m uvicorn test2.web:app --reload --app-dir src
```

2. 输入一个需要工具调用的问句，例如“北京天气怎么样”。
3. 确认 Think / Act / Observe 卡片顺序正确。
4. 确认最终回答只出现一次，并且是逐字输出的。
5. 确认最终 Think 卡片在回答文本前面。

## 八、经验总结

1. LangGraph `stream_mode="messages"` 会输出图中所有 LLM 调用的 token，必须显式标记“哪些 token 属于用户可见最终回复”。
2. 不要依赖 `AIMessageChunk.id == AIMessage.id` 来判断是否重复；不同模型供应商不保证一致。
3. 真流式和“严格只显示最终回复”不是互相排斥的，可以用显式 tag 在 LLM 调用层区分。
4. 前端在流式场景中不应无条件 `append()`；当卡片和文本可能乱序到达时，需要按业务语义决定插入位置。
