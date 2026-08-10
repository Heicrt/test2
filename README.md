# test2 — ReAct Agent (LangGraph)

基于 LangGraph 的最小化 ReAct Agent，在终端里进行多轮 Think → Act → Observe 对话，支持工具调用（计算器 + 实时天气查询）。

## 技术栈

| 层级 | 技术 |
|------|------|
| 图引擎 | LangGraph (StateGraph) |
| LLM 抽象 | LangChain Core |
| LLM 提供商 | Anthropic / OpenAI / DeepSeek / 智谱 / 阿里百炼 / Ollama / SiliconFlow / Groq |
| 运行时 | Python 3.10+ |

## 安装

```bash
cd test2

# uv（推荐）
uv sync
```
## 配置

1. 复制环境变量模板：

```bash
cp .env.example .env
```

> **注意**：`.env` 放在项目根目录，`config.py` 会**自动从项目根目录加载**，
> 与启动时的当前工作目录（cwd）无关。任意位置用 `uv run` 启动都能读到配置。

2. 编辑 `.env`，填入你的 API Key：

```env
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o
LLM_API_KEY=sk-your-key-here

# 可选：温度参数
LLM_TEMPERATURE=0
```

3. 如需使用天气工具，注册[和风天气 API](https://console.qweather.com)后填入：

```env
QWEATHER_API_HOST=your-api-host
QWEATHER_API_KEY=your-api-key
```

## 运行

> **提示**：本项目依赖由 `uv` 管理，统一用 `uv run` 启动（自动同步 `.venv` 并以可编辑模式安装本包）。
> 直接系统 `python` 运行会报 `ModuleNotFoundError: langgraph`。

```bash
# 使用 .env 中配置的供应商
uv run python -m test2 --app-dir src

# 命令行覆盖供应商(不建议)
uv run python -m test2 --app-dir src --provider zhipu
uv run python -m test2 --app-dir src --provider ollama
uv run python -m test2 --app-dir src --provider deepseek
```

> **兼容**：也可在 `src/` 目录内执行 `uv run python -m test2`（src 布局下更贴近包路径）。
> 无论哪种方式，`.env` 都从项目根目录固定加载，不会因目录不同而丢失配置。


## Web 可视化（对话交互界面）

在浏览器里与 Agent 对话，实时观察 Think → Act → Observe 每一步的执行过程。

```bash
# 启动 Web 服务（复用 .env 中的 LLM_PROVIDER）
uv run python -m uvicorn test2.web:app --reload --app-dir src

# 打开浏览器访问
# http://127.0.0.1:8000
```

> **注意**：`test2` 是 **src 布局** 包，代码在 `src/test2/` 下而非项目根目录。
> 直接 `python -m uvicorn test2.web:app --reload` 会因找不到 `test2` 模块而启动崩溃（浏览器报 `ERR_CONNECTION_REFUSED`）。
> 二选一解决：
> - 每次启动加 `--app-dir src`（如上命令，无需安装，推荐）
> - 或先 `uv sync`（安装为可编辑包，之后可省略 `--app-dir src`）

特性：
- 浏览器输入问题，替代终端 `input`
- 每个节点以彩色卡片实时渲染（Think=黄 / Act=红 / Observe=绿）
- 顶部 Think/Act/Observe 指示灯高亮当前执行的节点
- 基于 SSE 流式推送，节点级实时回放

现有 CLI（`python -m test2`）不受影响，Web 是并行入口。

## 多轮记忆

项目使用 LangGraph SQLite checkpointer 提供多轮对话记忆：

- **CLI**：默认使用 `cli-default` 会话，连续提问会保留上下文；输入 `clear` 或 `/clear` 清空当前会话记忆。
- **Web**：浏览器自动生成 `session_id` 并保存到 `localStorage`，后续消息复用同一会话；点击“清空”删除当前会话记忆。
- **上下文窗口**：`think_node` 只把最近 `MEMORY_WINDOW`（默认 20）条消息送入 LLM，避免长对话上下文无限膨胀。
- **动态摘要**：历史 token 超过 `SUMMARY_TOKEN_THRESHOLD`（默认 6000）时，用 LLM 压缩早期对话并更新会话摘要。
- **持久化**：对话 checkpoint 保存在 `data/memory.db`，服务重启后仍可恢复。
- **长期记忆**：每次完成最终回复后，LLM 会提取用户偏好、项目事实、关键决策等，保存到项目级 `data/memory.db`。

## 切换供应商

只需修改 `.env` 中的 `LLM_PROVIDER` 和 `LLM_MODEL`，无需改代码。

| 供应商 | LLM_PROVIDER | 示例模型 | 需要 API Key |
|--------|-------------|---------|-------------|
| Anthropic | `anthropic` | `claude-sonnet-4-20250514` | `LLM_API_KEY` |
| OpenAI | `openai` | `gpt-4o` | `LLM_API_KEY` |
| DeepSeek | `deepseek` | `deepseek-chat` | `LLM_API_KEY` |
| 智谱 | `zhipu` | `glm-4-flash` | `LLM_API_KEY` |
| 阿里百炼 | `dashscope` | `qwen-max` | `LLM_API_KEY` |
| Ollama | `ollama` | `qwen2.5` | 无需 |
| SiliconFlow | `siliconflow` | `Qwen/Qwen2.5-7B-Instruct` | `LLM_API_KEY` |
| Groq | `groq` | `llama-3.1-70b-versatile` | `LLM_API_KEY` |
| 自定义 | `custom` | 任意 | `LLM_API_KEY` + `LLM_BASE_URL` |

完整模型列表见 `src/test2/providers.toml`。

## 工作原理

```
用户输入 → think_node（AI分析+决策）
              ↓ should_act=True?
         act_node（执行工具）
              ↓
       observe_node（记录结果）
              ↓
         think_node（基于结果继续分析）
              ↓ should_act=False?
         END（返回最终回复）
```

## 可用工具

- **calculator** — 数学表达式计算（支持 `+ - * / ** sqrt sin cos tan log pi e`）
- **weather** — 实时天气查询（调用和风天气 API）

## 测试用例

```
你: 北京天气怎么样？
你: 1+2*3 等于多少？
你: 北京今天天气如何？如果温度升高3度是多少？
```

## 自动化测试

```bash
uv run pytest
```

## 项目结构

```
test2/
├── .env                    # 环境变量（不提交Git）
├── .env.example            # 环境变量模板
├── README.md
├── wiki.md                  # 逐行解析文档
├── pyproject.toml
├── tests/
│   ├── test_agent_session.py
│   └── test_memory_contracts.py
└── src/test2/
    ├── __main__.py          # CLI 运行入口
    ├── web.py               # Web 可视化入口（FastAPI + SSE）
    ├── config.py            # LLM 配置（查表模式）
    ├── providers.toml       # 供应商预设表
    ├── state.py             # ReActState 与 summary reducer
    ├── memory.py            # token 估算、摘要、消息裁剪
    ├── memory_contracts.py  # MemoryStore Protocol 与长期记忆常量
    ├── memory_store.py      # 项目级长期记忆 SQLite Store
    ├── graph.py             # LangGraph 图定义
    ├── runtime.py           # create_runtime、AgentRuntime
    ├── agent_session.py     # CLI/Web 共享 runner
    ├── tools.py             # 工具定义
    ├── static/
    │   └── index.html       # Web 前端（对话交互界面）
```
