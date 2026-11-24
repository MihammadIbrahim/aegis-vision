"""Request and response bodies of /api/v1/assistant/* (decision D-027). Field names are the contract of the web app
(``website/src/features/assistant/types.ts``) and of the SRS in the Organization Admin page specification."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.infrastructure.database.repositories.assistant_repository import MessageRow, SessionRow

EntityType = Literal["ALERT", "INCIDENT", "CAMERA", "EVIDENCE", "MANUAL_REPORT", "LOCATION"]
Provider = Literal["deterministic", "ollama", "openai_compatible"]


class _Out(BaseModel):
    model_config = ConfigDict(frozen=True)


class CreateSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=120)
    context_incident_id: UUID | None = Field(default=None, description="Start the chat about this case")


class SendMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=1000, description="The question (read-only; no uploads)")

    @field_validator("content")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Enter a question.")
        return value


class CitationResponse(_Out):
    entity_type: EntityType
    id: str
    label: str
    route: str = Field(description="Web app path of the record, e.g. /incidents/<id>")


class ToolCallResponse(_Out):
    tool: str
    arguments: dict[str, Any]
    outcome: Literal["OK", "EMPTY", "DENIED", "ERROR"]
    rows: int
    latency_ms: float
    error: str | None = None


class AssistantMessageResponse(_Out):
    id: UUID
    session_id: UUID
    role: Literal["USER", "ASSISTANT"]
    content: str
    citations: list[CitationResponse]
    tool_calls: list[ToolCallResponse]
    blocks: list[dict[str, Any]] = Field(description="metrics | table | bars | timeline | notice blocks")
    insufficient_evidence: bool
    provider: Provider | None
    model_name: str | None
    created_at: datetime

    @classmethod
    def of(cls, m: MessageRow) -> "AssistantMessageResponse":
        return cls(
            id=m.id,
            session_id=m.session_id,
            role=m.role,  # type: ignore[arg-type]
            content=m.content,
            citations=[CitationResponse(**c) for c in m.citations],
            tool_calls=[ToolCallResponse(**t) for t in m.tool_calls],
            blocks=m.blocks,
            insufficient_evidence=m.insufficient_evidence,
            provider=m.provider,  # type: ignore[arg-type]
            model_name=m.model_name,
            created_at=m.created_at,
        )


class AssistantSessionResponse(_Out):
    id: UUID
    title: str
    context_incident_id: UUID | None
    context_incident_reference: str | None
    created_at: datetime
    updated_at: datetime
    last_active_at: datetime
    message_count: int

    @classmethod
    def of(cls, s: SessionRow) -> "AssistantSessionResponse":
        return cls(
            id=s.id,
            title=s.title,
            context_incident_id=s.context_incident_id,
            context_incident_reference=s.context_incident_reference,
            created_at=s.created_at,
            updated_at=s.updated_at,
            last_active_at=s.last_active_at,
            message_count=s.message_count,
        )


class AssistantSessionDetailResponse(AssistantSessionResponse):
    messages: list[AssistantMessageResponse]


class AssistantExchangeResponse(_Out):
    session: AssistantSessionResponse
    user_message: AssistantMessageResponse
    assistant_message: AssistantMessageResponse


class CapabilityResponse(_Out):
    key: str
    title: str
    description: str
    examples: list[str]


class AssistantStatusResponse(_Out):
    provider: Provider
    provider_label: str
    model_name: str
    available: bool
    detail: str
    read_only: Literal[True] = True
    file_uploads: Literal[False] = False
    retention_days: int
    role: Literal["ADMIN", "SUPERVISOR"]
    capabilities: list[CapabilityResponse]
    tools: list[str]
    safety_note: str


class PurgeResponse(_Out):
    deleted: int
    retention_days: int
