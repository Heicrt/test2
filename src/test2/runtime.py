"""Agent 运行时组合：显式创建 checkpointer、store 和图。"""

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from test2.graph import build_graph
from test2.memory_store import DEFAULT_DB_PATH, LongTermMemoryStore


@dataclass
class AgentRuntime:
    """
    组合并持有 ReAct Agent 运行所需的图、checkpointer、长期记忆 Store 和 SQLite 连接。
    CLI/Web 直接使用 app 运行图、checkpointer 清空会话、memory_store 读写长期记忆；
    生命周期结束时调用 close 统一释放底层资源。
    """

    app: object
    checkpointer: SqliteSaver
    memory_store: LongTermMemoryStore
    db_path: Path
    conn: sqlite3.Connection

    def close(self):
        """
        关闭 AgentRuntime 持有的 SQLite 连接，释放长期记忆和 checkpointer 的底层资源。
        先关闭 memory_store，再关闭共享 conn；close 后不应继续使用本 runtime。

        Returns:
            None: 无返回值，资源释放结果通过后续连接状态体现。
        """
        self.memory_store.close()
        self.conn.close()


def create_runtime(
    provider: str = "anthropic",
    db_path: str | Path | None = None,
) -> AgentRuntime:
    """
    将 provider、checkpointer、长期记忆 Store 和编译图组装为可运行的 AgentRuntime。
    自动创建数据库目录、打开 SQLite 连接、初始化 SqliteSaver 与 LongTermMemoryStore，
    再调用 build_graph 编译图；所有共享依赖由 runtime 统一持有并负责关闭。

    Args:
        provider: 供应商名称，默认 "anthropic"；会传给 get_llm 决定模型实现。
        db_path: SQLite 数据库路径，默认 DEFAULT_DB_PATH；
            可为 str 或 Path，父目录不存在时会自动创建。

    Returns:
        AgentRuntime: 已包含 app、checkpointer、memory_store 和数据库连接的完整运行时。

    Raises:
        ValueError: provider 缺失、不在 providers.toml 或 protocol 不支持时触发。
        OSError: 无法创建数据库文件所在目录时触发。
        sqlite3.Error: 无法打开或初始化 SQLite 数据库时触发。
    """
    path = Path(db_path or DEFAULT_DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path), check_same_thread=False)
    checkpointer = SqliteSaver(conn)
    memory_store = LongTermMemoryStore(path)
    app = build_graph(
        provider,
        checkpointer=checkpointer,
        memory_store=memory_store,
    )
    return AgentRuntime(
        app=app,
        checkpointer=checkpointer,
        memory_store=memory_store,
        db_path=path,
        conn=conn,
    )
