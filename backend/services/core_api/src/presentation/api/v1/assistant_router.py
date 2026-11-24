"""AI Investigation & Security Analytics Chatbot (Organization Admin page 19, Supervisor page 15, incident page items
9–12; decision D-027). Read-only: it never changes a record and takes no uploads.

GET    /assistant/status                             provider, model, availability, capabilities
GET    /assistant/sessions                           the caller's own chats, most recent first
POST   /assistant/sessions                           {title?, context_incident_id?} → a new chat
GET    /assistant/sessions/{session_id}              the chat with its messages
DELETE /assistant/sessions/{session_id}              clear / delete the chat (audited)
POST   /assistant/sessions/{session_id}/messages     {content} → the question and the grounded answer (audited)
POST   /assistant/incidents/{incident_id}/investigate  timeline, related events and gaps of a case in its chat

Admins and Supervisors of the campus (Supervisors: operational scope only); Guards 403 STAFF_ONLY; Super Admin
tokens 403 SUPER_ADMIN_NOT_ALLOWED. Chats are private to their owner (Row-Level Security).

Internal (X-Service-Token; the storage retention daemon):
POST   /internal/assistant/purge-expired             delete chats inactive for longer than AI_CHAT_RETENTION_DAYS
"""

from typing import Annotated
from uuid import UUID

from assistant_engine import SAFETY_NOTE, AssistantEngine
from assistant_engine.catalog import CAPABILITIES, TOOLS
from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.dtos.assistant_dto import (
    AssistantExchangeResponse,
    AssistantMessageResponse,
    AssistantSessionDetailResponse,
    AssistantSessionResponse,
    AssistantStatusResponse,
    CapabilityResponse,
    CreateSessionRequest,
    PurgeResponse,
    SendMessageRequest,
)
from src.application.use_cases.assistant_use_cases import AssistantService, Exchange
from src.domain.entities.user import User
from src.infrastructure.database.repositories.assistant_repository import SqlAssistantRepository
from src.infrastructure.database.repositories.audit_log_repository import SqlOrganizationAuditLog
from src.infrastructure.database.unit_of_work import SqlTransaction
from src.presentation.api.v1.recording_router import require_service_token
from src.presentation.dependencies import SessionDep, StaffDep

router = APIRouter(prefix="/assistant", tags=["AI Investigation Chatbot"])
internal_router = APIRouter(prefix="/internal/assistant", tags=["Internal services"])


def _engine(request: Request) -> AssistantEngine:
    engine: AssistantEngine = request.app.state.assistant_engine
    return engine


def _service(actor: User, session: AsyncSession, request: Request) -> AssistantService:
    return AssistantService(
        _engine(request),
        SqlAssistantRepository(session, actor),
        SqlOrganizationAuditLog(session),
        SqlTransaction(session),
        actor,
        session,
    )


def _exchange(exchange: Exchange) -> AssistantExchangeResponse:
    return AssistantExchangeResponse(
        session=AssistantSessionResponse.of(exchange.session),
        user_message=AssistantMessageResponse.of(exchange.user_message),
        assistant_message=AssistantMessageResponse.of(exchange.assistant_message),
    )


@router.get("/status", response_model=AssistantStatusResponse)
async def assistant_status(actor: StaffDep, request: Request) -> AssistantStatusResponse:
    engine = _engine(request)
    state = await engine.provider.status()
    return AssistantStatusResponse(
        provider=engine.provider.name,  # type: ignore[arg-type]
        provider_label=engine.provider.label,
        model_name=engine.provider.model_name,
        available=state.available,
        detail=state.detail,
        retention_days=request.app.state.settings.ai_chat_retention_days,
        role="ADMIN" if actor.role.value == "ADMIN" else "SUPERVISOR",
        capabilities=[
            CapabilityResponse(key=c.key, title=c.title, description=c.description, examples=list(c.examples))
            for c in CAPABILITIES
        ],
        tools=[t.name for t in TOOLS],
        safety_note=SAFETY_NOTE,
    )


@router.get("/sessions", response_model=list[AssistantSessionResponse])
async def list_sessions(actor: StaffDep, session: SessionDep, request: Request) -> list[AssistantSessionResponse]:
    return [AssistantSessionResponse.of(s) for s in await _service(actor, session, request).sessions()]


@router.post("/sessions", response_model=AssistantSessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    body: CreateSessionRequest, actor: StaffDep, session: SessionDep, request: Request
) -> AssistantSessionResponse:
    created = await _service(actor, session, request).create(body.title, body.context_incident_id)
    return AssistantSessionResponse.of(created)


@router.get("/sessions/{session_id}", response_model=AssistantSessionDetailResponse)
async def get_session(
    session_id: UUID, actor: StaffDep, session: SessionDep, request: Request
) -> AssistantSessionDetailResponse:
    chat, messages = await _service(actor, session, request).get(session_id)
    return AssistantSessionDetailResponse(
        **AssistantSessionResponse.of(chat).model_dump(),
        messages=[AssistantMessageResponse.of(m) for m in messages],
    )


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(session_id: UUID, actor: StaffDep, session: SessionDep, request: Request) -> Response:
    await _service(actor, session, request).delete(session_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/sessions/{session_id}/messages", response_model=AssistantExchangeResponse, status_code=status.HTTP_201_CREATED
)
async def send_message(
    session_id: UUID, body: SendMessageRequest, actor: StaffDep, session: SessionDep, request: Request
) -> AssistantExchangeResponse:
    return _exchange(await _service(actor, session, request).ask(session_id, body.content))


@router.post(
    "/incidents/{incident_id}/investigate",
    response_model=AssistantExchangeResponse,
    status_code=status.HTTP_201_CREATED,
)
async def investigate_incident(
    incident_id: UUID, actor: StaffDep, session: SessionDep, request: Request
) -> AssistantExchangeResponse:
    return _exchange(await _service(actor, session, request).investigate(incident_id))


@internal_router.post("/purge-expired", response_model=PurgeResponse)
async def purge_expired(
    request: Request, session: SessionDep, _: Annotated[None, Depends(require_service_token)]
) -> PurgeResponse:
    days: int = request.app.state.settings.ai_chat_retention_days
    deleted = await session.scalar(text("SELECT app_ai_chat_purge(:days, 5000)"), {"days": days})
    await session.commit()
    return PurgeResponse(deleted=int(deleted or 0), retention_days=days)
