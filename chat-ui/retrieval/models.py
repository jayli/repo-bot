from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class RetrievalPlan:
    intent: str
    template: str
    entities: dict[str, Any] = field(default_factory=dict)
    queries: dict[str, list[Any]] = field(default_factory=dict)
    precision: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RetrievalHit:
    source: str
    repo: str
    path: str
    line_range: str
    content: str = ""
    strength: str = ""
    score: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EvidenceItem:
    id: str
    tier: str
    source: str
    repo: str
    path: str
    line_range: str
    claim: str
    content: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
