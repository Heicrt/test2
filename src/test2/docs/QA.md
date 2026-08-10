# QA：长期记忆的 LLM 输出如何写入数据库

## Q1：LLM 提炼记忆时，输出的是什么结构？

LLM 按 `EXTRACT_PROMPT` 的要求，输出一个 JSON 对象，包含 5 个固定 key，每个 key 对应一个事实列表：

```json
{
  "user_preferences":  ["喜欢简洁"],
  "project_facts":     ["用 SQLite"],
  "entities":          ["张三"],
  "key_decisions":     ["分三层"],
  "unfinished_tasks":  ["写文档"]
}
```

也就是说：**1 个对象，5 个 key，每个 key 挂一个 list**。

---

## Q2：这个 JSON 结构是怎么写入数据库的？

核心转换是：**1 个 JSON 对象 → 拆成最多 5 行记录**。

数据库 `long_term_memory` 表共 5 列：

| 列名 | 含义 | 是否允许空 |
|---|---|---|
| `scope` | 范围（如 `"project"`） | 不许空 |
| `category` | 分类（如 `"user_preferences"`） | 不许空 |
| `facts_json` | 事实列表的 JSON 字符串 | 不许空 |
| `source_thread_id` | 来源线程 ID | 可空 |
| `updated_at` | 更新时间 | 不许空 |

拆分过程由 `for category in CATEGORIES` 循环完成，5 个 key 依次变成 5 行：

```text
LLM 输出的 JSON（1 个 dict，5 个 key）
              ↓  for category in CATEGORIES 循环 5 次  ↓
数据库 long_term_memory 表（最多 5 行）
┌─────────┬────────────────────┬──────────────┬──────────────┬──────────┐
│ scope   │ category           │ facts_json   │ source_...   │ updated  │
├─────────┼────────────────────┼──────────────┼──────────────┼──────────┤
│ project │ user_preferences   │ ["喜欢简洁"]  │ thread-abc   │ 2026-... │
│ project │ project_facts      │ ["用 SQLite"] │ thread-abc   │ 2026-... │
│ project │ entities           │ ["张三"]      │ thread-abc   │ 2026-... │
│ project │ key_decisions      │ ["分三层"]    │ thread-abc   │ 2026-... │
│ project │ unfinished_tasks   │ ["写文档"]    │ thread-abc   │ 2026-... │
└─────────┴────────────────────┴──────────────┴──────────────┴──────────┘
```

---

## Q3：5 列分别从哪来？

只有 2 列来自 LLM 的 JSON，另外 3 列由代码自动补齐：

| 列 | 内容示例 | 来源 | 在 JSON 里有吗 |
|---|---|---|---|
| `scope` | `"project"` | 代码常量 `MEMORY_SCOPE` | ❌ 代码填的 |
| `category` | `"user_preferences"` | **JSON 的 key 名** | ✅ key |
| `facts_json` | `'["喜欢简洁"]'` | **JSON 的 value**（经 `json.dumps`） | ✅ value |
| `source_thread_id` | `"thread-abc"` | LangGraph config 取的当前会话 id | ❌ 代码填的 |
| `updated_at` | `2026-08-10T...` | 调用时 `_now()` 生成的时间戳 | ❌ 代码填的 |

LLM 只管"提炼什么事实、归到哪个类别"（2 列），其余 3 列由代码从常量和运行时上下文补齐。

---

## Q4：拆分循环的代码逻辑是什么？

```python
# src/test2/memory.py
response = llm.invoke(prompt)
data = parse_memory_json(response.content)
for category in CATEGORIES:
    facts = data.get(category)
    if isinstance(facts, list):
        memory_store.merge_facts(
            MEMORY_SCOPE,
            category,
            facts,
            source_thread_id=thread_id,
        )
```

逐行看转换过程：

```text
第 1 行  llm.invoke(prompt)
        → LLM 返回一段文本，内容是 JSON 字符串

第 2 行  data = parse_memory_json(response.content)
        → 把 JSON 字符串解析成 Python dict：
          {"user_preferences": ["喜欢简洁"], "project_facts": ["用 SQLite"], ...}

第 3 行  for category in CATEGORIES:
        → CATEGORIES 是 5 个固定类别名的元组
        → 循环 5 次，category 依次是 "user_preferences" / "project_facts" / ...

第 4 行  facts = data.get(category)
        → 从 dict 里取出这个类别对应的事实列表
        → 第一次循环：facts = ["喜欢简洁"]
        → 第二次循环：facts = ["用 SQLite"]
        → ...

第 5-8 行  memory_store.merge_facts(MEMORY_SCOPE, category, facts, ...)
        → 每次循环写一行：
           MEMORY_SCOPE  → scope 列
           category      → category 列
           facts         → 进 merge_facts 后 json.dumps → facts_json 列
           thread_id     → source_thread_id 列
           _now()        → updated_at 列（在 merge_facts 内部填）
```

---

## Q5：能走一个完整例子吗？

假设用户说了「以后回答简洁点」，对话结束触发 `extract`：

```text
1. LLM 收到 EXTRACT_PROMPT + 对话内容
   输出：'{"user_preferences": ["回答简洁"], "project_facts": [], ...}'

2. parse_memory_json 解析成 dict：
   data = {"user_preferences": ["回答简洁"], "project_facts": [], ...}

3. 循环第 1 次：category = "user_preferences"
   facts = ["回答简洁"]
   merge_facts("project", "user_preferences", ["回答简洁"], "thread-abc")
     ├─ scope            = "project"
     ├─ category         = "user_preferences"
     ├─ facts_json       = json.dumps(["回答简洁"]) = '["回答简洁"]'
     ├─ source_thread_id = "thread-abc"
     └─ updated_at       = "2026-08-10T12:00:00+00:00"
   → 写入一行

4. 循环第 2 次：category = "project_facts"
   facts = []  ← 空列表
   merge_facts 内部 if not facts: return  ← 空列表直接跳过，不写
   （所以空类别不产生行）

5. ... 其余 3 次循环，同样跳过空类别

6. 最终 memory.db 里只多了 1 行：
   | project | user_preferences | ["回答简洁"] | thread-abc | 2026-... |
```

---

## Q6：有两个关键细节需要注意

**1. 空类别不写库**

`merge_facts` 开头有 `if not facts: return`——LLM 返回的 5 个 key 里如果某个是空数组，这一类就不写库，不会产生 `facts_json = "[]"` 的空行。所以表里的行数 **≤ 5**，实际多少看 LLM 提炼出几类。

**2. 同一类别反复写入是覆盖，不是新增**

主键是 `(scope, category)`，第二次写 `user_preferences` 会触发 `ON CONFLICT DO UPDATE`——把旧的 `facts_json` 覆盖成合并去重后的新列表，不会出现两行 `user_preferences`。

---

## 一句话总结

LLM 吐一个"5 个 key 的 JSON 对象"，代码用 `for category in CATEGORIES` 循环把它**拆成最多 5 次 `merge_facts` 调用**——每次调用写一行，JSON 的 key 变成 `category` 列、value 变成 `facts_json` 列，另外 3 列（`scope` / `source_thread_id` / `updated_at`）由代码从常量和运行时上下文自动补齐。
