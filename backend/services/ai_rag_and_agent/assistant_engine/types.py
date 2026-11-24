"""Plain data types shared by the planner, the tools (implemented in core_api) and the synthesizer (decision D-027).

Everything here is JSON-serialisable through ``to_dict`` so it can be stored in ``ai_chat_messages`` and returned by
the API. Nothing here touches a database: the engine only plans, asks the caller's executor to run read-only tools
under the caller's Row-Level Security, and writes the answer from what the tools returned.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol

Role = Literal["ADMIN", "SUPERVISOR"]
EntityType = Literal["ALERT", "INCIDENT", "CAMERA", "EVIDENCE", "MANUAL_REPORT", "LOCATION"]
Outcome = Literal["OK", "EMPTY", "DENIED", "ERROR"]
LocationLevel = Literal["BUILDING", "FLOOR", "AREA"]

ENTITY_TYPES: tuple[EntityType, ...] = ("ALERT", "INCIDENT", "CAMERA", "EVIDENCE", "MANUAL_REPORT", "LOCATION")


def route_for(entity_type: EntityType, entity_id: str) -> str:
    """The web app page of a record (Organization Admin and Supervisor web app routes)."""
    return {
        "ALERT": f"/alerts/{entity_id}",
        "INCIDENT": f"/incidents/{entity_id}",
        "CAMERA": f"/cameras?camera={entity_id}",
        "EVIDENCE": f"/evidence/{entity_id}",
        "MANUAL_REPORT": f"/manual-reports/{entity_id}",
        "LOCATION": f"/locations?location={entity_id}",
    }[entity_type]


@dataclass(frozen=True, slots=True)
class Citation:
    """A clickable reference to a record an answer used."""

    entity_type: EntityType
    id: str
    label: str
    route: str

    @classmethod
    def of(cls, entity_type: EntityType, entity_id: object, label: str) -> Citation:
        return cls(entity_type, str(entity_id), label[:160], route_for(entity_type, str(entity_id)))

    def to_dict(self) -> dict[str, str]:
        return {"entity_type": self.entity_type, "id": self.id, "label": self.label, "route": self.route}


@dataclass(frozen=True, slots=True)
class LocationRef:
    level: LocationLevel
    id: str
    name: str  # display path, e.g. "Admin Block › Ground › Lobby"

    def to_dict(self) -> dict[str, str]:
        return {"level": self.level, "id": self.id, "name": self.name}

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> LocationRef | None:
        if not value:
            return None
        return cls(value["level"], str(value["id"]), str(value["name"]))


@dataclass(frozen=True, slots=True)
class CameraRef:
    id: str
    name: str


@dataclass(frozen=True, slots=True)
class Vocabulary:
    """What the planner may match words against: the caller's own campus only (read by core_api under RLS)."""

    now: datetime
    time_zone: str
    role: Role
    locations: tuple[LocationRef, ...] = ()
    cameras: tuple[CameraRef, ...] = ()
    report_types: tuple[str, ...] = ()
    evidence_tags: tuple[str, ...] = ()


@dataclass(slots=True)
class ToolCall:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "arguments": self.arguments}


@dataclass(slots=True)
class ToolResult:
    """What one read-only tool returned. ``data`` holds only figures and labels computed by SQL."""

    name: str
    arguments: dict[str, Any]
    outcome: Outcome
    data: dict[str, Any] = field(default_factory=dict)
    citations: list[Citation] = field(default_factory=list)
    rows: int = 0
    latency_ms: float = 0.0
    error: str | None = None

    def summary(self) -> dict[str, Any]:
        """For ``tool_calls`` in the stored message and the "Tools & queries used" drawer (no raw rows)."""
        return {
            "tool": self.name,
            "arguments": self.arguments,
            "outcome": self.outcome,
            "rows": self.rows,
            "latency_ms": round(self.latency_ms, 1),
            **({"error": self.error} if self.error else {}),
        }


class ToolExecutor(Protocol):
    """Implemented by core_api: runs a catalogue tool with the caller's database session (RLS applies)."""

    async def run(self, call: ToolCall) -> ToolResult: ...


@dataclass(slots=True)
class EngineAnswer:
    content: str
    blocks: list[dict[str, Any]]
    citations: list[Citation]
    results: list[ToolResult]
    insufficient_evidence: bool
    context: dict[str, Any]
    provider: str
    model_name: str
    refusal: str | None = None  # ACCUSATION | READ_ONLY | NO_UPLOADS | EXCLUDED | None
    phrasing: str = "deterministic"  # deterministic | llm | deterministic_fallback

    def tool_calls(self) -> list[dict[str, Any]]:
        return [r.summary() for r in self.results]
