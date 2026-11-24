"""In-app voice calls about an alert or a case (decision D-025). Role-compatible: Guards, Supervisors and Admins of
the campus who may see the alert or case; Super Admin tokens 403; another campus's alert, case or call 404.

GET    /alerts/{alert_id}/calls/active        {voice_calls_available, provider, call | null}
POST   /alerts/{alert_id}/calls               start a call, or join the one already active: {outcome, call, connection}
GET    /incidents/{incident_id}/calls/active
POST   /incidents/{incident_id}/calls
GET    /calls                                 active calls that ring for me or that I joined (polling fallback)
GET    /calls/{call_id}
POST   /calls/{call_id}/join                  {outcome: JOINED, call, connection}
POST   /calls/{call_id}/decline               a ringing invitation
POST   /calls/{call_id}/leave                 {call, call_ended}; the call ends when nobody is left
POST   /calls/{call_id}/end                   for everyone: Admins, Supervisors, or whoever started it

With VOICE_CALL_PROVIDER=none, starting and joining answer 503 VOICE_CALL_NOT_CONFIGURED (nothing is written).
"""

from uuid import UUID

from fastapi import APIRouter, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.dtos.call_dto import (
    ActiveCallResponse,
    CallJoinResponse,
    CallSessionResponse,
    LeaveCallResponse,
    VoiceConnectionResponse,
)
from src.application.use_cases.call_use_cases import CallService, JoinedCall
from src.domain.entities.call import CallScope, CallSession, CallTarget
from src.domain.entities.user import User
from src.domain.value_objects.enums import UserRole
from src.infrastructure.database.repositories.audit_log_repository import SqlOrganizationAuditLog
from src.infrastructure.database.repositories.call_repository import SqlCallRepository
from src.infrastructure.database.unit_of_work import SqlTransaction
from src.presentation.dependencies import MemberDep, SessionDep

alert_router = APIRouter(prefix="/alerts", tags=["Voice calls"])
incident_router = APIRouter(prefix="/incidents", tags=["Voice calls"])
router = APIRouter(prefix="/calls", tags=["Voice calls"])


def _service(request: Request, actor: User, session: AsyncSession) -> CallService:
    return CallService(
        SqlCallRepository(session, actor),
        SqlOrganizationAuditLog(session),
        SqlTransaction(session),
        request.app.state.voice_provider,
    )


def _out(call: CallSession, actor: User) -> CallSessionResponse:
    return CallSessionResponse.of(call, actor.id, is_staff=actor.role in (UserRole.ADMIN, UserRole.SUPERVISOR))


def _joined(joined: JoinedCall, actor: User) -> CallJoinResponse:
    return CallJoinResponse(
        outcome=joined.outcome,  # type: ignore[arg-type]
        call=_out(joined.call, actor),
        connection=VoiceConnectionResponse.of(joined.grant),
    )


async def _active(request: Request, actor: User, session: AsyncSession, target: CallTarget) -> ActiveCallResponse:
    service = _service(request, actor, session)
    call = await service.active(actor, target)
    voice = request.app.state.voice_provider
    return ActiveCallResponse(
        voice_calls_available=voice.configured,
        provider=voice.provider,
        call=None if call is None else _out(call, actor),
    )


async def _start(
    request: Request, response: Response, actor: User, session: AsyncSession, target: CallTarget
) -> CallJoinResponse:
    joined = await _service(request, actor, session).start(actor, target)
    response.status_code = status.HTTP_201_CREATED if joined.outcome == "STARTED" else status.HTTP_200_OK
    return _joined(joined, actor)


@alert_router.get("/{alert_id}/calls/active", response_model=ActiveCallResponse)
async def alert_active_call(
    alert_id: UUID, request: Request, actor: MemberDep, session: SessionDep
) -> ActiveCallResponse:
    return await _active(request, actor, session, CallTarget(CallScope.ALERT, alert_id=alert_id))


@alert_router.post("/{alert_id}/calls", response_model=CallJoinResponse, status_code=status.HTTP_201_CREATED)
async def start_alert_call(
    alert_id: UUID, request: Request, response: Response, actor: MemberDep, session: SessionDep
) -> CallJoinResponse:
    return await _start(request, response, actor, session, CallTarget(CallScope.ALERT, alert_id=alert_id))


@incident_router.get("/{incident_id}/calls/active", response_model=ActiveCallResponse)
async def incident_active_call(
    incident_id: UUID, request: Request, actor: MemberDep, session: SessionDep
) -> ActiveCallResponse:
    return await _active(request, actor, session, CallTarget(CallScope.INCIDENT, incident_id=incident_id))


@incident_router.post("/{incident_id}/calls", response_model=CallJoinResponse, status_code=status.HTTP_201_CREATED)
async def start_incident_call(
    incident_id: UUID, request: Request, response: Response, actor: MemberDep, session: SessionDep
) -> CallJoinResponse:
    return await _start(request, response, actor, session, CallTarget(CallScope.INCIDENT, incident_id=incident_id))


@router.get("", response_model=list[CallSessionResponse])
async def my_calls(request: Request, actor: MemberDep, session: SessionDep) -> list[CallSessionResponse]:
    return [_out(c, actor) for c in await _service(request, actor, session).mine(actor)]


@router.get("/{call_id}", response_model=CallSessionResponse)
async def get_call(call_id: UUID, request: Request, actor: MemberDep, session: SessionDep) -> CallSessionResponse:
    return _out(await _service(request, actor, session).get(actor, call_id), actor)


@router.post("/{call_id}/join", response_model=CallJoinResponse)
async def join_call(call_id: UUID, request: Request, actor: MemberDep, session: SessionDep) -> CallJoinResponse:
    return _joined(await _service(request, actor, session).join(actor, call_id), actor)


@router.post("/{call_id}/decline", response_model=CallSessionResponse)
async def decline_call(call_id: UUID, request: Request, actor: MemberDep, session: SessionDep) -> CallSessionResponse:
    return _out(await _service(request, actor, session).decline(actor, call_id), actor)


@router.post("/{call_id}/leave", response_model=LeaveCallResponse)
async def leave_call(call_id: UUID, request: Request, actor: MemberDep, session: SessionDep) -> LeaveCallResponse:
    call, ended = await _service(request, actor, session).leave(actor, call_id)
    return LeaveCallResponse(call=_out(call, actor), call_ended=ended)


@router.post("/{call_id}/end", response_model=CallSessionResponse)
async def end_call(call_id: UUID, request: Request, actor: MemberDep, session: SessionDep) -> CallSessionResponse:
    return _out(await _service(request, actor, session).end(actor, call_id), actor)
