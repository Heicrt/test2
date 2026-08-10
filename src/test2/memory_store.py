"""项目级长期记忆的 SQLite 存储。"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from test2.memory_contracts import MEMORY_SCOPE

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "memory.db"

MAX_FACTS_PER_CATEGORY = 100


class LongTermMemoryStore:
    """基于 SQLite 的长期记忆 Store。"""

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

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

        existing = self.get_facts(scope, category)
        merged = list(existing)
        for fact in facts:
            text = str(fact).strip()
            if text and text not in merged:
                merged.append(text)

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

        try:
            data = json.loads(row["facts_json"])
        except json.JSONDecodeError:
            return []

        if not isinstance(data, list):
            return []
        return [str(item) for item in data]

    def get_memory_context(self, scope: str = MEMORY_SCOPE) -> str:
        """返回按类别组织好的长期记忆文本。"""
        rows = self.conn.execute(
            """
            SELECT category
            FROM long_term_memory
            WHERE scope = ?
            ORDER BY category
            """,
            (scope,),
        ).fetchall()

        parts = []
        for row in rows:
            category = row["category"]
            facts = self.get_facts(scope, category)
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
