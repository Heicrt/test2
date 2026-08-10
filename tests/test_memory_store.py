import pytest

from test2.memory_store import LongTermMemoryStore


@pytest.fixture
def store(tmp_path):
    memory_store = LongTermMemoryStore(tmp_path / "memory.db")
    yield memory_store
    memory_store.close()


def test_get_memory_context_empty(store):
    assert store.get_memory_context() == ""


def test_get_memory_context_groups_and_sorts_categories(store):
    store.merge_facts(
        "project",
        "user_preferences",
        ["用户喜欢简洁回答", "用户叫小明"],
    )
    store.merge_facts(
        "project",
        "project_facts",
        ["项目使用 Python"],
    )

    assert store.get_memory_context("project") == (
        "project_facts:\n"
        "- 项目使用 Python\n"
        "\n"
        "user_preferences:\n"
        "- 用户喜欢简洁回答\n"
        "- 用户叫小明"
    )


def test_get_memory_context_skips_malformed_json(store):
    store.merge_facts("project", "project_facts", ["项目使用 Python"])
    store.conn.execute(
        """
        INSERT INTO long_term_memory (
            scope,
            category,
            facts_json,
            source_thread_id,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            "project",
            "broken",
            "not-json",
            None,
            "2026-08-10T00:00:00+00:00",
        ),
    )
    store.conn.commit()

    assert store.get_facts("project", "broken") == []
    assert store.get_memory_context("project") == (
        "project_facts:\n"
        "- 项目使用 Python"
    )


def test_get_facts_and_context_share_parsing_rule(store):
    store.conn.execute(
        """
        INSERT INTO long_term_memory (
            scope,
            category,
            facts_json,
            source_thread_id,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            "project",
            "entities",
            '["test2", 1]',
            None,
            "2026-08-10T00:00:00+00:00",
        ),
    )
    store.conn.commit()

    assert store.get_facts("project", "entities") == ["test2", "1"]
    assert store.get_memory_context("project") == "entities:\n- test2\n- 1"
