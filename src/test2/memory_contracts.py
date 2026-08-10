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
    """Minimal interface used by graph-facing memory functions."""

    def get_memory_context(self, scope: str = MEMORY_SCOPE) -> str: ...

    def merge_facts(
        self,
        scope: str,
        category: str,
        facts: list,
        source_thread_id: str | None = None,
    ) -> None: ...
