"""项目级长期记忆的 SQLite 存储。"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from test2.memory_contracts import MEMORY_SCOPE

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "memory.db"

MAX_FACTS_PER_CATEGORY = 100


def _decode_facts_json(raw: str) -> list[str]:
    """
    将数据库中的 facts_json 文本解析为事实字符串列表。
    自动处理损坏 JSON、非 list 根结构和 None 输入，统一返回空列表；
    list 内元素统一转为字符串，保证调用方拿到稳定 list[str]。

    Args:
        raw: long_term_memory.facts_json 列内容，可能为损坏文本或非 JSON 数据。

    Returns:
        list[str]: 解析后的事实列表；损坏或非 list 数据返回空列表。
    """
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    return [str(item) for item in data]


class LongTermMemoryStore:
    """
    实现 MemoryStore 的 SQLite 长期记忆存储。
    负责创建 long_term_memory 表、按 scope/category 合并去重事实、
    读取可注入 LLM 的记忆文本，并通过 close 释放连接；
    与 SqliteSaver 共用同一 memory.db 文件，但使用独立 SQLite 连接。
    """

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH):
        """
        将数据库路径转换为可用的 LongTermMemoryStore 实例。
        自动创建父目录、打开 SQLite 连接并设置 Row 工厂，
        然后初始化 long_term_memory 表；check_same_thread=False 允许多线程访问。

        Args:
            db_path: SQLite 数据库路径，默认 DEFAULT_DB_PATH；
                可为 str 或 Path，父目录不存在时会自动创建。

        Returns:
            None: 无返回值，初始化完成后实例可直接读写长期记忆。

        Raises:
            OSError: 无法创建数据库文件所在目录时触发。
            sqlite3.Error: 无法打开或初始化 SQLite 数据库时触发。
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)  # 数据库文件所在文件夹不存在就创建
        #打开 SQLite 文件；check_same_thread=False 允许多线程访问
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        # 设置行工厂为 Row 类型，方便直接访问列值
        self.conn.row_factory = sqlite3.Row

        self._init_schema()

    def _init_schema(self):
        """
        初始化 long_term_memory 表结构，确保存储可写入。
        使用 CREATE TABLE IF NOT EXISTS 创建以 (scope, category) 为主键的表
        并提交事务；重复初始化不会报错。

        Returns:
            None: 无返回值，建表副作用在数据库文件中持久化。
        """
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
        )#以上注释是给SQLite看的，造一张表，名叫 long_term_memory；如果已经造过了就跳过，别报错
        #第 1 列叫 scope（范围），存文本，不许留空。
        #第 2 列叫 category（分类），存文本，不许留空。
        #第 3 列叫 facts_json（事实 JSON），存文本，不许留空。
        #第 4 列叫 source_thread_id（来源线程 ID），存文本，可空。
        #第 5 列叫 updated_at（更新时间），存文本，不许留空。

        
        #提交事务后，数据库文件才会被写入
        self.conn.commit()

    def merge_facts(
        self,
        scope: str,
        category: str,
        facts: list,
        source_thread_id: str | None = None,
    ):
        """
        将新事实合并写入指定 scope/category 的长期记忆记录。
        facts 为空时直接返回；否则读取旧事实、按精确文本去重、
        保留最近 MAX_FACTS_PER_CATEGORY 条，再以 INSERT ... ON CONFLICT DO UPDATE
        写回并提交事务。

        Args:
            scope: 记忆作用域，决定写入哪一组长期记忆。
            category: 事实类别，来自 CATEGORIES 固定集合。
            facts: 本次新增事实列表；空列表表示不需要写入。
            source_thread_id: 可选来源会话 ID，仅记录事实来源，不参与去重。

        Returns:
            None: 无返回值，写入结果通过后续 get_facts 或 get_memory_context 读取。
        """
        if not facts:
            return

        #get_facts 读取数据库里已经存在的旧事实
        existing = self.get_facts(scope, category)
        #复制旧列表
        merged = list(existing)
        #如果事实不为空且不在现有事实中，则添加到merged列表中
        for fact in facts:
            text = str(fact).strip()
            if text and text not in merged:
                merged.append(text)
        #保留最新 MAX_FACTS_PER_CATEGORY 条事实    
        merged = merged[-MAX_FACTS_PER_CATEGORY:]
        self.conn.execute(
            """
            INSERT INTO long_term_memory (
                scope,
                category,
                facts_json,
                source_thread_id,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(scope, category) DO UPDATE SET
                facts_json = excluded.facts_json,
                source_thread_id = excluded.source_thread_id,
                updated_at = excluded.updated_at
            """,
            (
                scope,
                category,
                json.dumps(merged, ensure_ascii=False),
                source_thread_id,
                self._now(),
            ),
        )
        self.conn.commit()

    def get_facts(self, scope: str, category: str) -> list[str]:
        """
        将指定 scope/category 的 facts_json 读取为事实字符串列表。
        无记录时返回空列表；记录存在时使用 _decode_facts_json 统一解析，
        损坏数据返回空列表而不抛出异常。

        Args:
            scope: 记忆作用域，与写入时一致。
            category: 事实类别，与写入时一致。

        Returns:
            list[str]: 该类别下的事实列表；无记录或数据损坏时为空列表。
        """
        row = self.conn.execute(
            """
            SELECT facts_json
            FROM long_term_memory
            WHERE scope = ? AND category = ?
            """,
            (scope, category),
        ).fetchone()
        if not row:
            return []

        return _decode_facts_json(row["facts_json"])

    def get_memory_context(self, scope: str = MEMORY_SCOPE) -> str:
        """
        将指定 scope 下全部长期记忆转换为按类别组织的文本。
        一次查询 category 和 facts_json 并按 category 排序，
        跳过空类别和损坏 JSON；输出格式适合直接放入 SystemMessage。

        Args:
            scope: 记忆作用域，默认项目级 MEMORY_SCOPE。

        Returns:
            str: 按类别组织的长期记忆文本；无记忆时返回空字符串。
        """
        rows = self.conn.execute(
            """
            SELECT category, facts_json
            FROM long_term_memory
            WHERE scope = ?
            ORDER BY category
            """,
            (scope,),
        ).fetchall()

        parts = []
        for row in rows:
            category = row["category"]
            facts = _decode_facts_json(row["facts_json"])
            if facts:
                parts.append(f"{category}:\n- " + "\n- ".join(facts))
        return "\n\n".join(parts)

    def clear_scope(self, scope: str = MEMORY_SCOPE):
        """
        删除指定 scope 下的全部长期记忆记录。
        执行 DELETE 并提交事务；只影响 long_term_memory，
        不删除 checkpointer 保存的会话数据。

        Args:
            scope: 要清空的记忆作用域，默认项目级 MEMORY_SCOPE。

        Returns:
            None: 无返回值，清空结果通过后续 get_memory_context 读取。
        """
        self.conn.execute(
            "DELETE FROM long_term_memory WHERE scope = ?",
            (scope,),
        )
        self.conn.commit()

    def close(self):
        """
        关闭 LongTermMemoryStore 持有的 SQLite 连接。
        关闭后当前实例不应继续读写；重复关闭由 sqlite3 自行处理。

        Returns:
            None: 无返回值，资源释放结果通过后续连接状态体现。
        """
        self.conn.close()

    @staticmethod
    def _now() -> str:
        """
        生成当前 UTC 时间的 ISO 8601 字符串。
        使用 timezone.utc 保证 updated_at 时间统一、可排序。

        Returns:
            str: 形如 2026-08-11T00:00:00+00:00 的当前 UTC 时间字符串。
        """
        return datetime.now(timezone.utc).isoformat()
