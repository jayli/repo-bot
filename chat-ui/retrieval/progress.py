from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ProgressRecord:
    id: int
    status: str
    label: str
    detail: str = ""


class ProgressLog:
    def __init__(self, max_items: int = 6):
        self.max_items = max_items
        self._next_id = 1
        self._records: list[ProgressRecord] = []

    def start(self, label: str, detail: str = "") -> int:
        record_id = self._next_id
        self._next_id += 1
        self._records.append(ProgressRecord(record_id, "running", label, detail))
        self._trim()
        return record_id

    def complete(self, record_id: int) -> None:
        record = self._find(record_id)
        if record:
            record.status = "done"

    def fail(self, record_id: int, error: str) -> None:
        record = self._find(record_id)
        label = record.label if record else "工具调用"
        self._records.append(ProgressRecord(self._next_id, "error", label, error))
        self._next_id += 1
        self._trim()

    def error(self, label: str, error: str) -> None:
        self._records.append(ProgressRecord(self._next_id, "error", label, error))
        self._next_id += 1
        self._trim()

    def lines(self) -> list[str]:
        return [self._format(record) for record in self._records[-self.max_items :]]

    def _find(self, record_id: int) -> ProgressRecord | None:
        for record in self._records:
            if record.id == record_id:
                return record
        return None

    def _trim(self) -> None:
        if len(self._records) > self.max_items:
            self._records = self._records[-self.max_items :]

    def _format(self, record: ProgressRecord) -> str:
        marker = {"running": "→", "done": "✓", "error": "×"}.get(record.status, "→")
        line = f"{marker} **{record.label}**"
        if record.detail:
            line += f"：{record.detail}"
        return line
