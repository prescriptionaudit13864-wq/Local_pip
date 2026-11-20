from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

SECTIONS = ("Demo", "CC", "Hx", "Dx", "Inv", "Rx", "Adv")


@dataclass(slots=True)
class Finding:
    section: str
    concern_id: str
    evidence: str
    why: str
    correction: str
    severity: str = "medium"
    confidence: float = 0.5
    related_to: str | None = None
    likely_redundant: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
