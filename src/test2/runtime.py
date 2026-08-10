"""Agent 运行时组合：显式创建 checkpointer、store 和图。"""

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from test2.graph import build_graph
from test2.memory_store import DEFAULT_DB_PATH, LongTermMemoryStore


@dataclass
class AgentRuntime:
    """一个可运行的 Agent 实例及其共享依赖。"""

    app: object
    checkpointer: SqliteSaver
    memory_store: LongTermMemoryStore
    db_path: Path
    conn: sqlite3.Connection

    def close(self):
        """关闭运行时持有的 SQLite 连接。"""
        self.memory_store.close()
        self.conn.close()


def create_runtime(
    provider: str = "anthropic",
    db_path: str | Path | None = None,
) -> AgentRuntime:
    """显式创建 Agent 运行时，不在模块 import 阶段产生文件系统副作用。"""
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
