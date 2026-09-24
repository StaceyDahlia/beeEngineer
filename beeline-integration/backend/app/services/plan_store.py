from __future__ import annotations

import copy
import threading
import uuid
from dataclasses import dataclass
from typing import Any


class PlanStoreError(RuntimeError):
    status_code = 400
    code = "PLAN_STORE_ERROR"


class PlanNotFoundError(PlanStoreError):
    status_code = 404
    code = "PLAN_NOT_FOUND"


class PlanConflictError(PlanStoreError):
    status_code = 409
    code = "PLAN_CONFLICT"


@dataclass
class _Preview:
    preview_id: str
    lineage_id: str
    base_plan_id: str
    base_version: int
    event_id: str
    event: dict[str, Any]
    plan: dict[str, Any]
    diff: dict[str, Any]
    applied: bool = False


class PlanStore:
    """Минимальное локальное хранилище версий и одноразовых preview.

    ASSUMPTION MVP: состояние живёт только в памяти одного процесса FastAPI и
    сбрасывается при перезапуске приложения.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.clear()

    def clear(self) -> None:
        with getattr(self, "_lock", threading.RLock()):
            self._plans: dict[str, dict[str, Any]] = {}
            self._lineage_by_plan: dict[str, str] = {}
            self._current_by_lineage: dict[str, str] = {}
            self._history_by_lineage: dict[str, list[str]] = {}
            self._previews: dict[str, _Preview] = {}
            self._applied_events: dict[tuple[str, str], str] = {}

    @staticmethod
    def _id(prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex}"

    def create_plan(self, plan: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            plan_id = self._id("plan")
            lineage_id = self._id("lineage")
            stored = copy.deepcopy(plan)
            stored.update(plan_id=plan_id, version=1, parent_plan_id=None)
            self._plans[plan_id] = stored
            self._lineage_by_plan[plan_id] = lineage_id
            self._current_by_lineage[lineage_id] = plan_id
            self._history_by_lineage[lineage_id] = [plan_id]
            return copy.deepcopy(stored)

    def get_plan(self, plan_id: str) -> dict[str, Any]:
        with self._lock:
            try:
                return copy.deepcopy(self._plans[plan_id])
            except KeyError as exc:
                raise PlanNotFoundError(f"План {plan_id} не найден.") from exc

    def ensure_current(self, plan_id: str, version: int) -> str:
        with self._lock:
            if plan_id not in self._plans:
                raise PlanNotFoundError(f"План {plan_id} не найден.")
            lineage_id = self._lineage_by_plan[plan_id]
            current_id = self._current_by_lineage[lineage_id]
            current = self._plans[current_id]
            if current_id != plan_id or int(current["version"]) != int(version):
                raise PlanConflictError(
                    "Предпросмотр устарел: текущая версия плана уже изменилась."
                )
            return lineage_id

    def save_preview(
        self,
        *,
        base_plan_id: str,
        base_version: int,
        event: dict[str, Any],
        plan: dict[str, Any],
        diff: dict[str, Any],
    ) -> dict[str, Any]:
        with self._lock:
            lineage_id = self.ensure_current(base_plan_id, base_version)
            event_id = str(event["event_id"])
            if (lineage_id, event_id) in self._applied_events:
                raise PlanConflictError(f"Событие {event_id} уже применено.")
            if any(
                item.lineage_id == lineage_id
                and item.event_id == event_id
                and not item.applied
                for item in self._previews.values()
            ):
                raise PlanConflictError(f"Для события {event_id} уже создан preview.")

            preview_id = self._id("preview")
            candidate = copy.deepcopy(plan)
            candidate.update(
                plan_id=None,
                version=int(base_version) + 1,
                parent_plan_id=base_plan_id,
                event=copy.deepcopy(event),
                diff=copy.deepcopy(diff),
            )
            preview = _Preview(
                preview_id=preview_id,
                lineage_id=lineage_id,
                base_plan_id=base_plan_id,
                base_version=int(base_version),
                event_id=event_id,
                event=copy.deepcopy(event),
                plan=candidate,
                diff=copy.deepcopy(diff),
            )
            self._previews[preview_id] = preview
            return self._preview_response(preview)

    def get_preview(self, preview_id: str) -> dict[str, Any]:
        with self._lock:
            try:
                preview = self._previews[preview_id]
            except KeyError as exc:
                raise PlanNotFoundError(f"Preview {preview_id} не найден.") from exc
            return self._preview_response(preview)

    def apply_preview(self, preview_id: str) -> dict[str, Any]:
        with self._lock:
            try:
                preview = self._previews[preview_id]
            except KeyError as exc:
                raise PlanNotFoundError(f"Preview {preview_id} не найден.") from exc
            if preview.applied:
                raise PlanConflictError("Этот preview уже применён.")
            self.ensure_current(preview.base_plan_id, preview.base_version)
            event_key = (preview.lineage_id, preview.event_id)
            if event_key in self._applied_events:
                raise PlanConflictError(f"Событие {preview.event_id} уже применено.")

            plan_id = self._id("plan")
            stored = copy.deepcopy(preview.plan)
            stored.update(
                plan_id=plan_id,
                version=preview.base_version + 1,
                parent_plan_id=preview.base_plan_id,
                event=copy.deepcopy(preview.event),
                diff=copy.deepcopy(preview.diff),
            )
            self._plans[plan_id] = stored
            self._lineage_by_plan[plan_id] = preview.lineage_id
            self._current_by_lineage[preview.lineage_id] = plan_id
            self._history_by_lineage[preview.lineage_id].append(plan_id)
            self._applied_events[event_key] = plan_id
            preview.applied = True
            return copy.deepcopy(stored)

    def history(self, plan_id: str) -> list[dict[str, Any]]:
        with self._lock:
            if plan_id not in self._plans:
                raise PlanNotFoundError(f"План {plan_id} не найден.")
            lineage_id = self._lineage_by_plan[plan_id]
            return [
                copy.deepcopy(self._plans[item_id])
                for item_id in self._history_by_lineage[lineage_id]
            ]

    @staticmethod
    def _preview_response(preview: _Preview) -> dict[str, Any]:
        return {
            "preview_id": preview.preview_id,
            "event_id": preview.event_id,
            "base_plan_id": preview.base_plan_id,
            "base_version": preview.base_version,
            "proposed_version": preview.base_version + 1,
            "applied": preview.applied,
            "event": copy.deepcopy(preview.event),
            "diff": copy.deepcopy(preview.diff),
            "plan": copy.deepcopy(preview.plan),
        }


plan_store = PlanStore()
