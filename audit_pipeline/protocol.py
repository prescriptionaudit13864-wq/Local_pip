from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .models import SECTIONS


@dataclass(frozen=True, slots=True)
class Protocol:
    role: str
    section_names: dict[str, str]
    fields: dict[str, list[str]]
    concerns: dict[str, list[dict[str, Any]]]
    followup_only: frozenset[str]
    order: tuple[str, ...]


def resolve_protocol_path(path: str | Path | None = None) -> Path:
    """Resolve an explicit or conventional V3 protocol location."""
    if path is not None:
        source = Path(path).expanduser().resolve()
        if source.is_file():
            return source
        raise FileNotFoundError(f"Protocol file not found at the provided path: {source}")

    package_dir = Path(__file__).resolve().parent
    candidates = (
        package_dir.parent / "protocol.yaml",
        package_dir / "configs" / "protocol.yaml",
        package_dir / "protocol.yaml",
    )
    for source in candidates:
        if source.is_file():
            return source

    searched = "\n  - ".join(str(source) for source in candidates)
    raise FileNotFoundError(
        "No protocol.yaml was found. Provide --protocol PATH or place the file "
        "in one of these locations:\n  - " + searched
    )


def load_protocol(path: str | Path | None = None) -> Protocol:
    source = resolve_protocol_path(path)
    with source.open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    missing = [key for key in ("auditor_role", "sections", "agent_fields", "concerns") if key not in raw]
    if missing:
        raise ValueError(f"Protocol missing required keys: {', '.join(missing)}")
    for section in SECTIONS:
        if section not in raw["sections"] or section not in raw["agent_fields"] or section not in raw["concerns"]:
            raise ValueError(f"Protocol is incomplete for section {section}")
    return Protocol(
        role=str(raw["auditor_role"]).strip(),
        section_names=dict(raw["sections"]),
        fields={key: list(value) for key, value in raw["agent_fields"].items()},
        concerns={key: list(value) for key, value in raw["concerns"].items()},
        followup_only=frozenset(raw.get("followup_only_concerns", [])),
        order=tuple(raw.get("section_order", SECTIONS)),
    )
