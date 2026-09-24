from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class PlanHistoryRepository(Protocol):
    """Интерфейс истории планов для будущей замены in-memory реализации БД."""

    def create_plan(self, plan: dict[str, Any]) -> dict[str, Any]: ...

    def history(self, plan_id: str) -> list[dict[str, Any]]: ...

