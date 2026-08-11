"""Memory provider contract and shared long-term memory constants."""

from typing import Protocol, runtime_checkable

MEMORY_SCOPE = "project"

CATEGORIES = (
    "user_preferences",
    "project_facts",
    "entities",
    "key_decisions",
    "unfinished_tasks",
)


@runtime_checkable
class MemoryStore(Protocol):
    """
    定义图面记忆模块所需的项目级长期记忆读写接口。
    该协议只约束 get_memory_context 和 merge_facts 两个能力，
    LongTermMemoryStore 结构性实现；memory.py 依赖此接口而非具体 SQLite 类。
    """

    def get_memory_context(self, scope: str = MEMORY_SCOPE) -> str:
        """
        将指定作用域的长期记忆读取为可注入 LLM 的文本。
        内部由实现决定按类别组织、过滤空类别和格式化；无记忆时返回空字符串。

        Args:
            scope: 记忆作用域，默认项目级 MEMORY_SCOPE；实现按此筛选长期记忆记录。

        Returns:
            str: 可直接放入 SystemMessage 的长期记忆文本，无记忆时为空字符串。
        """
        ...

    def merge_facts(
        self,
        scope: str,
        category: str,
        facts: list,
        source_thread_id: str | None = None,
    ) -> None:
        """
        将新提取的事实合并写入指定作用域和类别的长期记忆。
        内部由实现负责去重、数量上限和持久化；
        调用方不读取返回值，写入成功后 get_memory_context 可读到。

        Args:
            scope: 记忆作用域，决定写入哪一组长期记忆。
            category: 事实类别，来自 CATEGORIES 固定集合。
            facts: 本次提取的新事实列表；空列表表示没有需要写入的事实。
            source_thread_id: 可选的来源会话 ID，仅用于追踪事实来源。

        Returns:
            None: 无返回值，写入结果通过后续读取观察。
        """
        ...
