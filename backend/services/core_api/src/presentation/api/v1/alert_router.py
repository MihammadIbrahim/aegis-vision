"""Alert Management (decision D-013).

Detection intake (AI workers; header ``X-Detection-Token``):
POST   /internal/detections                 create, merge or ignore (duplicate) an alert

Signed-in organization users (staff see their campus's alerts; a Guard sees alerts sent to them):
GET    /alerts                              list (?status=, ?priority=, ?alert_type=, ?building_id=, …, ?q=,
                                            ?since=, ?until=, ?handled_by=)
GET    /alerts/{id}                         details and recipients
GET    /alerts/{id}/history                 the response history
GET    /alerts/{id}/responders              staff and recipient Guards: who acknowledged / is responding (D-025)
GET    /alerts/{id}/escalations             staff: secondary broadcasts and the Guards each added (D-024)
GET    /alerts/{id}/snapshot                staff: the detection snapshot (or a frame of the recording)
GET    /alerts/{id}/clip                    staff: the event clip cut from the recording (not saved; audited)
PUT    /alerts/{id}/handler                 staff: the responsible Admin or Supervisor (D-024)
POST   /internal/alerts/escalate-overdue    services: automatic escalation now (X-Service-Token)
POST   /alerts/{id}/acknowledge             Guard recipient
POST   /alerts/{id}/responding              Guard recipient ("I'm Responding")
POST   /alerts/{id}/assessment              Guard recipient: UNDER_CONTROL / ADDITIONAL_GUARDS_REQUIRED
POST   /alerts/{id}/resolve                 Admin or Supervisor
POST   /alerts/{id}/dismiss                 Admin or Supervisor (reason required)
POST   /alerts/{id}/escalate                Admin or Supervisor: more Guards of the same campus
GET    /alerts/{id}/messages                the alert's chat
POST   /alerts/{id}/messages
POST   /guard/sync                          a Guard's queued offline actions, reports and messages
"""

import hmac
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from src.application.dtos.alert_dto import (
    ActionResponse,
    AlertEscalationResponse,
    AlertEventResponse,
    AlertResponse,
    AssessmentRequest,
    ClientAction,
    DetectionRequest,
    DetectionResponse,
    DismissRequest,
    EscalateRequest,
    EscalationResponse,
    HandlerRequest,
    MessageRequest,
    MessageResponse,
    ResolveRequest,
)
from src.application.dtos.call_dto import ResponderResponse
from src.application.dtos.manual_report_dto import ManualReportRequest
from src.application.use_cases.alert_use_cases import (
    AlertChat,
    AlertHandling,
    CloseAlert,
    EscalateAlert,
    GuardAlertAction,
    IngestDetection,
    ViewAlerts,
)
from src.application.use_cases.manual_report_use_cases import SubmitManualReport
from src.config.settings import Settings
from src.domain.entities.alert import AlertStatus, GuardAction
from src.domain.entities.alert_escalation import EscalationRequest
from src.domain.entities.evidence import EvidenceKind
from src.domain.entities.user import User
from src.domain.exceptions.domain_errors import (
    AuthenticationFailed,
    DomainError,
    ServiceUnavailable,
)
from src.domain.repositories.i_alert_repo import AlertFilter, Detection
from src.infrastructure.database.connection import bind_actor
from src.infrastructure.database.repositories.alert_repository import SqlAlertOperations, SqlAlertRepository
from src.infrastructure.database.repositories.audit_log_repository import (
    SqlOrganizationAuditLog,
)
from src.infrastructure.database.repositories.incident_repository import SqlIncidentRepository
from src.infrastructure.database.repositories.location_repository import (
    SqlLocationRepository,
)
from src.infrastructure.database.repositories.manual_report_repository import (
    SqlManualReportRepository,
)
from src.infrastructure.database.unit_of_work import SqlTransaction
from src.presentation.api.v1.evidence_router import vault as evidence_vault
from src.presentation.api.v1.recording_router import require_service_token
from src.presentation.dependencies import GuardDep, MemberDep, SessionDep, StaffDep
from src.presentation.error_handlers import status_for

internal_router = APIRouter(prefix="/internal", tags=["Detection intake"])
router = APIRouter(prefix="/alerts", tags=["Alerts"])
sync_router = APIRouter(prefix="/guard", tags=["Guard app"])


def _settings(request: Request) -> Settings:
    return request.app.state.settings


@internal_router.post("/detections", response_model=DetectionResponse)
async def ingest_detection(
    body: DetectionRequest,
    request: Request,
    session: SessionDep,
    x_detection_token: Annotated[str | None, Header()] = None,
) -> DetectionResponse:
    settings = _settings(request)
    expected = settings.detection_ingest_token.get_secret_value() if settings.detection_ingest_token else ""
    if not expected:
        raise ServiceUnavailable("The detection intake is not configured.", code="DETECTION_INTAKE_DISABLED")
    if not x_detection_token or not hmac.compare_digest(x_detection_token.encode(), expected.encode()):
        raise AuthenticationFailed("Invalid detection token.")
    detection = Detection(
        camera_id=body.camera_id,
        alert_type=body.detection_type.value,
        confidence=body.confidence,
        detected_at=body.detected_at,
        detection_id=body.detection_id,
        snapshot_ref=body.snapshot_ref,
    )
    result = await IngestDetection(
        SqlAlertRepository(session), SqlTransaction(session), dedup_seconds=settings.alert_dedup_seconds
    ).execute(detection)
    return DetectionResponse(alert_id=result.alert_id, outcome=result.outcome)  # type: ignore[arg-type]


# --- Reading ---------------------------------------------------------------------------------------------


@router.get("", response_model=list[AlertResponse])
async def list_alerts(
    actor: MemberDep,
    session: SessionDep,
    status: AlertStatus | None = None,
    priority: Literal["HIGH", "STANDARD"] | None = None,
    alert_type: Literal["WEAPON", "FIGHT_VIOLENCE", "RESTRICTED_AREA"] | None = None,
    building_id: UUID | None = None,
    floor_id: UUID | None = None,
    area_id: UUID | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    q: str | None = Query(default=None, max_length=200, description="Alert ID, type, camera or location"),
    since: datetime | None = None,
    until: datetime | None = None,
    handled_by: UUID | None = None,
) -> list[AlertResponse]:
    where = AlertFilter(
        status=None if status is None else status.value,
        priority=priority,
        alert_type=alert_type,
        building_id=building_id,
        floor_id=floor_id,
        area_id=area_id,
        limit=limit,
        offset=offset,
        text=q,
        since=since,
        until=until,
        handled_by=handled_by,
    )
    return [AlertResponse.of(a) for a in await ViewAlerts(SqlAlertRepository(session)).list_alerts(actor, where)]


@router.get("/{alert_id}", response_model=AlertResponse)
async def get_alert(alert_id: UUID, actor: MemberDep, session: SessionDep) -> AlertResponse:
    return AlertResponse.of(await ViewAlerts(SqlAlertRepository(session)).get(actor, alert_id))


def _handling(actor: User, session: AsyncSession) -> AlertHandling:
    return AlertHandling(
        SqlAlertRepository(session),
        SqlOrganizationAuditLog(session),
        SqlTransaction(session),
        SqlAlertOperations(session),
        SqlIncidentRepository(session, actor),
    )


@router.get("/{alert_id}/escalations", response_model=list[AlertEscalationResponse])
async def alert_escalations(alert_id: UUID, actor: StaffDep, session: SessionDep) -> list[AlertEscalationResponse]:
    found = await _handling(actor, session).escalations(actor, alert_id)
    return [
        AlertEscalationResponse(
            id=e.id,
            scope=e.scope,
            escalated_by_name=e.escalated_by_name,
            escalated_by_role=e.escalated_by_role,
            note=e.note,
            added_count=e.added_count,
            created_at=e.created_at,
            guards=e.guards,
        )
        for e in found
    ]


@router.put("/{alert_id}/handler", response_model=AlertResponse)
async def assign_handler(alert_id: UUID, body: HandlerRequest, actor: StaffDep, session: SessionDep) -> AlertResponse:
    return AlertResponse.of(await _handling(actor, session).assign_handler(actor, alert_id, body.user_id))


async def _preview(
    alert_id: UUID, kind: EvidenceKind, request: Request, actor: User, session: AsyncSession
) -> FileResponse:
    folder = Path(tempfile.mkdtemp(prefix="alert-preview-"))
    output = folder / ("snapshot.jpg" if kind is EvidenceKind.SNAPSHOT else "clip.mp4")
    try:
        await evidence_vault(actor, session, request).alert_preview(actor, alert_id, kind, output)
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return FileResponse(
        output,
        media_type="image/jpeg" if kind is EvidenceKind.SNAPSHOT else "video/mp4",
        headers={"Cache-Control": "no-store"},
        background=BackgroundTask(shutil.rmtree, folder, ignore_errors=True),
    )


@router.get("/{alert_id}/snapshot")
async def alert_snapshot(alert_id: UUID, request: Request, actor: StaffDep, session: SessionDep) -> FileResponse:
    return await _preview(alert_id, EvidenceKind.SNAPSHOT, request, actor, session)


@router.get("/{alert_id}/clip")
async def alert_clip(alert_id: UUID, request: Request, actor: StaffDep, session: SessionDep) -> FileResponse:
    return await _preview(alert_id, EvidenceKind.CLIP, request, actor, session)


@internal_router.post("/alerts/escalate-overdue")
async def escalate_overdue(
    session: SessionDep, _: Annotated[None, Depends(require_service_token)]
) -> dict[str, int]:
    escalated = await SqlAlertOperations(session).escalate_overdue()
    await session.commit()
    return {"escalated": escalated}


@router.get("/{alert_id}/history", response_model=list[AlertEventResponse])
async def alert_history(alert_id: UUID, actor: MemberDep, session: SessionDep) -> list[AlertEventResponse]:
    events = await ViewAlerts(SqlAlertRepository(session)).history(actor, alert_id)
    return [AlertEventResponse.of(e) for e in events]


@router.get("/{alert_id}/responders", response_model=list[ResponderResponse])
async def alert_responders(alert_id: UUID, actor: MemberDep, session: SessionDep) -> list[ResponderResponse]:
    """The responder roster (Guard app screen 6 item 6; decision D-025): Guards who acknowledged or are responding,
    for the campus's staff and the alert's Guard recipients. Guard IDs are not shown."""
    await ViewAlerts(SqlAlertRepository(session)).get(actor, alert_id)  # 404 when the caller may not see it
    rows = (
        await session.execute(
            text(
                "SELECT full_name, state, assessment, reason, acknowledged_at, responding_at, is_me "
                "FROM app_alert_responders(:alert)"
            ),
            {"alert": alert_id},
        )
    ).all()
    return [ResponderResponse.model_validate(r._asdict()) for r in rows]


# --- Guard actions -----------------------------------------------------------------------------------


async def _guard_action(
    alert_id: UUID,
    action: GuardAction,
    client_action_id: UUID | None,
    guard: User,
    session,  # type: ignore[no-untyped-def]
) -> ActionResponse:
    result = await GuardAlertAction(SqlAlertRepository(session), SqlTransaction(session)).execute(
        guard, alert_id, action, client_action_id
    )
    return ActionResponse(alert_id=alert_id, outcome=result.outcome, status=result.status)  # type: ignore[arg-type]


@router.post("/{alert_id}/acknowledge", response_model=ActionResponse)
async def acknowledge(
    alert_id: UUID, guard: GuardDep, session: SessionDep, body: ClientAction | None = None
) -> ActionResponse:
    return await _guard_action(
        alert_id, GuardAction.ACKNOWLEDGE, body.client_action_id if body else None, guard, session
    )


@router.post("/{alert_id}/responding", response_model=ActionResponse)
async def responding(
    alert_id: UUID, guard: GuardDep, session: SessionDep, body: ClientAction | None = None
) -> ActionResponse:
    return await _guard_action(
        alert_id, GuardAction.RESPONDING, body.client_action_id if body else None, guard, session
    )


@router.post("/{alert_id}/assessment", response_model=ActionResponse)
async def assessment(alert_id: UUID, body: AssessmentRequest, guard: GuardDep, session: SessionDep) -> ActionResponse:
    return await _guard_action(alert_id, GuardAction(body.assessment), body.client_action_id, guard, session)


# --- Staff actions ------------------------------------------------------------------------------------


def _staff_deps(session):  # type: ignore[no-untyped-def]
    return SqlAlertRepository(session), SqlOrganizationAuditLog(session), SqlTransaction(session)


@router.post("/{alert_id}/resolve", response_model=AlertResponse)
async def resolve(
    alert_id: UUID, actor: StaffDep, session: SessionDep, body: ResolveRequest | None = None
) -> AlertResponse:
    body = body or ResolveRequest()
    alert = await CloseAlert(*_staff_deps(session)).execute(
        actor, alert_id, AlertStatus.RESOLVED, body.note, body.client_action_id
    )
    return AlertResponse.of(alert)


@router.post("/{alert_id}/dismiss", response_model=AlertResponse)
async def dismiss(alert_id: UUID, body: DismissRequest, actor: StaffDep, session: SessionDep) -> AlertResponse:
    alert = await CloseAlert(*_staff_deps(session)).execute(
        actor, alert_id, AlertStatus.DISMISSED, body.reason, body.client_action_id
    )
    return AlertResponse.of(alert)


@router.post("/{alert_id}/escalate", response_model=EscalationResponse)
async def escalate(alert_id: UUID, body: EscalateRequest, actor: StaffDep, session: SessionDep) -> EscalationResponse:
    use_case = EscalateAlert(
        SqlAlertRepository(session),
        SqlOrganizationAuditLog(session),
        SqlTransaction(session),
        SqlLocationRepository(session, actor.organization_id, actor.campus_id),
    )
    request = EscalationRequest(
        scope=body.scope,
        location=None if body.location is None else body.location.to_selection(),
        guard_ids=body.guard_ids,
        note=body.note,
    )
    alert, added = await use_case.execute(actor, alert_id, request, body.client_action_id)
    return EscalationResponse(alert=AlertResponse.of(alert), added_guards=added)


# --- Chat ------------------------------------------------------------------------------------------------


@router.get("/{alert_id}/messages", response_model=list[MessageResponse])
async def messages(alert_id: UUID, actor: MemberDep, session: SessionDep) -> list[MessageResponse]:
    chat = AlertChat(*_staff_deps(session))
    return [MessageResponse.of(m) for m in await chat.list_messages(actor, alert_id)]


@router.post("/{alert_id}/messages", response_model=list[MessageResponse], status_code=201)
async def post_message(
    alert_id: UUID, body: MessageRequest, actor: MemberDep, session: SessionDep
) -> list[MessageResponse]:
    chat = AlertChat(*_staff_deps(session))
    return [MessageResponse.of(m) for m in await chat.post(actor, alert_id, body.body, body.client_message_id)]


# --- Offline sync (Guard app) -------------------------------------------------------------------------------


class SyncAlertAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["ALERT_ACTION"]
    client_action_id: UUID
    alert_id: UUID
    action: GuardAction


class SyncReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["MANUAL_REPORT"]
    report: ManualReportRequest


class SyncMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["ALERT_MESSAGE"]
    client_message_id: UUID
    alert_id: UUID
    body: str = Field(max_length=2000)


class SyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[Annotated[SyncAlertAction | SyncReport | SyncMessage, Field(discriminator="kind")]] = Field(
        min_length=1, max_length=50
    )


class SyncItemResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    index: int
    kind: str
    ok: bool
    http_status: int
    code: str | None = None
    detail: str | None = None
    outcome: str | None = None
    target_id: UUID | None = None


@sync_router.post("/sync", response_model=list[SyncItemResult])
async def sync(body: SyncRequest, request: Request, guard: GuardDep, session: SessionDep) -> list[SyncItemResult]:
    """Processes the queue in order. Each item is applied on its own; one failing item does not stop
    the others, and resending an item already applied changes nothing."""
    settings = _settings(request)
    results: list[SyncItemResult] = []
    for index, item in enumerate(body.items):
        await bind_actor(session, guard.id)  # every item commits on its own
        try:
            if isinstance(item, SyncAlertAction):
                result = await GuardAlertAction(SqlAlertRepository(session), SqlTransaction(session)).execute(
                    guard, item.alert_id, item.action, item.client_action_id
                )
                results.append(
                    SyncItemResult(
                        index=index,
                        kind=item.kind,
                        ok=True,
                        http_status=200,
                        outcome=result.outcome,
                        target_id=item.alert_id,
                    )
                )
            elif isinstance(item, SyncReport):
                report, created = await SubmitManualReport(
                    SqlManualReportRepository(session),
                    request.app.state.media_storage,
                    SqlTransaction(session),
                    max_photo_bytes=settings.manual_report_photo_max_bytes,
                ).execute(guard, item.report.to_draft())
                results.append(
                    SyncItemResult(
                        index=index,
                        kind=item.kind,
                        ok=True,
                        http_status=201 if created else 200,
                        outcome="CREATED" if created else "REPLAYED",
                        target_id=report.id,
                    )
                )
            else:
                await AlertChat(*_staff_deps(session)).post(guard, item.alert_id, item.body, item.client_message_id)
                results.append(
                    SyncItemResult(
                        index=index, kind=item.kind, ok=True, http_status=201, outcome="POSTED", target_id=item.alert_id
                    )
                )
        except DomainError as error:
            await session.rollback()
            results.append(
                SyncItemResult(
                    index=index,
                    kind=item.kind,
                    ok=False,
                    http_status=status_for(error),
                    code=error.code,
                    detail=str(error),
                )
            )
    return results
