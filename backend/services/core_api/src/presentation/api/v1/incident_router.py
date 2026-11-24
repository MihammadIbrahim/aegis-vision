"""Incidents & Investigations (Org Admin page 6, Supervisor page 6; decision D-024). Admins and Supervisors of
the campus; Guards only read and write the chat of cases they take part in; Super Admin tokens 403.

GET    /incidents                                  ?status= (repeatable), priority, assigned (me | none | user ID),
                                                   q, since, until, limit, offset
POST   /incidents                                  open a case (optionally assigned; may link alerts, reports, evidence)
GET    /incidents/assignable-staff                 Admins and Supervisors of the campus
GET    /incidents/for-alert/{alert_id}             the cases an alert is linked to
GET    /incidents/{id}                             details, links, participants, footage gaps, history
PUT    /incidents/{id}                             title, description, priority, time, camera, location
POST   /incidents/{id}/status                      {status, note}
PUT    /incidents/{id}/assignee                    {user_id | null}
POST   /incidents/{id}/notes                       {note}
PUT    /incidents/{id}/alerts/{alert_id}           link (DELETE: unlink); also /reports/{id}, /evidence/{id}
PUT    /incidents/{id}/participants/{guard_id}     invite a Guard to the chat (DELETE: remove)
GET    /incidents/{id}/messages                    ?after=  (staff, or a Guard taking part)
POST   /incidents/{id}/messages                    {body}
GET    /guard/incidents                            a Guard's cases (chat only)
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.dtos.incident_dto import (
    AssigneeRequest,
    CreateIncidentRequest,
    IncidentDetailResponse,
    IncidentPageResponse,
    IncidentRequest,
    IncidentResponse,
    MessageRequest,
    MessageResponse,
    NoteRequest,
    StaffMemberResponse,
    StatusChangeRequest,
)
from src.application.use_cases.incident_use_cases import IncidentService
from src.domain.entities.incident import IncidentPriority, IncidentStatus
from src.domain.entities.user import User
from src.domain.repositories.i_incident_repo import IncidentFilter
from src.infrastructure.database.repositories.audit_log_repository import SqlOrganizationAuditLog
from src.infrastructure.database.repositories.incident_repository import SqlIncidentRepository
from src.infrastructure.database.repositories.location_repository import SqlLocationRepository
from src.infrastructure.database.unit_of_work import SqlTransaction
from src.presentation.dependencies import GuardDep, MemberDep, SessionDep, StaffDep

router = APIRouter(prefix="/incidents", tags=["Incidents & Investigations"])
guard_router = APIRouter(prefix="/guard/incidents", tags=["Guard app"])

LinkKind = Literal["alerts", "reports", "evidence"]
_KIND = {"alerts": "alert", "reports": "report", "evidence": "evidence"}


def service(actor: User, session: AsyncSession) -> IncidentService:
    return IncidentService(
        SqlIncidentRepository(session, actor),
        SqlLocationRepository(session, actor.organization_id, actor.campus_id),
        SqlOrganizationAuditLog(session),
        SqlTransaction(session),
    )


@router.get("", response_model=IncidentPageResponse)
async def list_incidents(
    actor: StaffDep,
    session: SessionDep,
    status_: list[IncidentStatus] | None = Query(default=None, alias="status"),
    priority: IncidentPriority | None = None,
    assigned: str | None = Query(default=None, max_length=64, description="me, none, or a user ID"),
    q: str | None = Query(default=None, max_length=200),
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> IncidentPageResponse:
    assigned_to: UUID | None = None
    if assigned == "me":
        assigned_to = actor.id
    elif assigned not in (None, "", "none"):
        try:
            assigned_to = UUID(assigned)
        except ValueError:
            assigned_to = None
    where = IncidentFilter(
        statuses=tuple(s.value for s in status_) if status_ else None,
        priority=priority.value if priority else None,
        assigned_to=assigned_to,
        unassigned=assigned == "none",
        text=q,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )
    return IncidentPageResponse.of(await service(actor, session).search(actor, where), limit, offset)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=IncidentResponse)
async def open_incident(body: CreateIncidentRequest, actor: StaffDep, session: SessionDep) -> IncidentResponse:
    incident = await service(actor, session).create(
        actor,
        body.to_details(),
        assigned_to=body.assigned_to,
        alert_ids=body.alert_ids,
        report_ids=body.report_ids,
        evidence_ids=body.evidence_ids,
    )
    return IncidentResponse.of(incident)


@router.get("/assignable-staff", response_model=list[StaffMemberResponse])
async def assignable_staff(actor: StaffDep, session: SessionDep) -> list[StaffMemberResponse]:
    return [StaffMemberResponse.of(s) for s in await service(actor, session).assignable_staff(actor)]


@router.get("/for-alert/{alert_id}", response_model=list[IncidentResponse])
async def incidents_of_alert(alert_id: UUID, actor: StaffDep, session: SessionDep) -> list[IncidentResponse]:
    return [IncidentResponse.of(i) for i in await service(actor, session).of_alert(actor, alert_id)]


@router.get("/{incident_id}", response_model=IncidentDetailResponse)
async def incident_detail(incident_id: UUID, actor: StaffDep, session: SessionDep) -> IncidentDetailResponse:
    return IncidentDetailResponse.of(await service(actor, session).detail(actor, incident_id))


@router.put("/{incident_id}", response_model=IncidentResponse)
async def edit_incident(
    incident_id: UUID, body: IncidentRequest, actor: StaffDep, session: SessionDep
) -> IncidentResponse:
    return IncidentResponse.of(await service(actor, session).update(actor, incident_id, body.to_details()))


@router.post("/{incident_id}/status", response_model=IncidentResponse)
async def change_status(
    incident_id: UUID, body: StatusChangeRequest, actor: StaffDep, session: SessionDep
) -> IncidentResponse:
    incident = await service(actor, session).change_status(actor, incident_id, body.status, body.note)
    return IncidentResponse.of(incident)


@router.put("/{incident_id}/assignee", response_model=IncidentResponse)
async def assign(incident_id: UUID, body: AssigneeRequest, actor: StaffDep, session: SessionDep) -> IncidentResponse:
    return IncidentResponse.of(await service(actor, session).assign(actor, incident_id, body.user_id))


@router.post("/{incident_id}/notes", status_code=status.HTTP_204_NO_CONTENT)
async def add_note(incident_id: UUID, body: NoteRequest, actor: StaffDep, session: SessionDep) -> None:
    await service(actor, session).add_note(actor, incident_id, body.note)


@router.put("/{incident_id}/participants/{guard_user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def add_participant(incident_id: UUID, guard_user_id: UUID, actor: StaffDep, session: SessionDep) -> None:
    await service(actor, session).add_participant(actor, incident_id, guard_user_id)


@router.delete("/{incident_id}/participants/{guard_user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_participant(incident_id: UUID, guard_user_id: UUID, actor: StaffDep, session: SessionDep) -> None:
    await service(actor, session).remove_participant(actor, incident_id, guard_user_id)


@router.put("/{incident_id}/{kind}/{target_id}", response_model=IncidentResponse)
async def link(
    incident_id: UUID, kind: LinkKind, target_id: UUID, actor: StaffDep, session: SessionDep
) -> IncidentResponse:
    return IncidentResponse.of(await service(actor, session).link(actor, incident_id, _KIND[kind], target_id))


@router.delete("/{incident_id}/{kind}/{target_id}", response_model=IncidentResponse)
async def unlink(
    incident_id: UUID, kind: LinkKind, target_id: UUID, actor: StaffDep, session: SessionDep
) -> IncidentResponse:
    return IncidentResponse.of(await service(actor, session).unlink(actor, incident_id, _KIND[kind], target_id))


@router.get("/{incident_id}/messages", response_model=list[MessageResponse])
async def messages(
    incident_id: UUID,
    actor: MemberDep,
    session: SessionDep,
    after: datetime | None = None,
    limit: int = Query(default=200, ge=1, le=500),
) -> list[MessageResponse]:
    found = await service(actor, session).messages(actor, incident_id, after=after, limit=limit)
    return [MessageResponse.of(m) for m in found]


@router.post("/{incident_id}/messages", status_code=status.HTTP_201_CREATED, response_model=MessageResponse)
async def post_message(
    incident_id: UUID, body: MessageRequest, actor: MemberDep, session: SessionDep
) -> MessageResponse:
    return MessageResponse.of(await service(actor, session).post_message(actor, incident_id, body.body))


@guard_router.get("", response_model=list[IncidentResponse])
async def guard_incidents(actor: GuardDep, session: SessionDep) -> list[IncidentResponse]:
    return [IncidentResponse.of(i) for i in await service(actor, session).guard_incidents(actor)]
