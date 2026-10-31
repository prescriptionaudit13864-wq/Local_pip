from __future__ import annotations

from dataclasses import asdict, dataclass, field
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

    @classmethod
    def from_dict(cls, value: dict[str, Any], expected_section: str) -> "Finding":
        severity = str(value.get("severity", "medium")).lower()
        if severity not in {"low", "medium", "high", "critical"}:
            severity = "medium"
        try:
            confidence = min(1.0, max(0.0, float(value.get("confidence", 0.5))))
        except (TypeError, ValueError):
            confidence = 0.5
        return cls(
            section=expected_section,
            concern_id=str(value.get("concern_id", value.get("id", "unknown"))).strip(),
            evidence=str(value.get("evidence", "")).strip(),
            why=str(value.get("why", "")).strip(),
            correction=str(value.get("correction", "")).strip(),
            severity=severity,
            confidence=confidence,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SectionResult:
    section: str
    findings: list[Finding] = field(default_factory=list)
    error: str | None = None
    attempts: int = 1
    elapsed_seconds: float = 0.0


@dataclass(slots=True)
class AuditResult:
    record_id: str
    visit_type: str
    status: str
    findings: list[Finding]
    summary: list[str]
    sections: dict[str, SectionResult]
    elapsed_seconds: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "3.0",
            "record_id": self.record_id,
            "visit_type": self.visit_type,
            "status": self.status,
            "findings": [item.to_dict() for item in self.findings],
            "summary": self.summary,
            "sections": {name: asdict(result) for name, result in self.sections.items()},
            "elapsed_seconds": round(self.elapsed_seconds, 3),
        }

