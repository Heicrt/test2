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
    """把 facts_json 列解析为事实字符串列表；损坏数据返回空列表。"""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    return [str(item) for item in data]


class LongTermMemoryStore:
    """基于 SQLite 的长期记忆 Store。"""

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)  # 数据库文件所在文件夹不存在就创建
        #打开 SQLite 文件；check_same_thread=False 允许多线程访问
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        # 设置行工厂为 Row 类型，方便直接访问列值
        self.conn.row_factory = sqlite3.Row

        self._init_schema()

    def _init_schema(self):# 初始化数据库表，_表示私有方法
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
        """追加新事实，按精确文本去重，单类最多保留 100 条。"""
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
        """返回按类别组织好的长期记忆文本。"""
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
        self.conn.execute(
            "DELETE FROM long_term_memory WHERE scope = ?",
            (scope,),
        )
        self.conn.commit()

    def close(self):
        self.conn.close()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
