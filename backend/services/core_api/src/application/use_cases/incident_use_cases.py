"""Incidents & Investigations (requirements Sections 15.5 and 16; Org Admin page 6, Supervisor page 6;
decision D-024).

Admins and Supervisors of a campus open cases, link AI alerts, Guards' manual reports and saved evidence,
assign a responsible Admin or Supervisor, add notes, move the status and talk in the case chat with the
Guards taking part. Every action is kept in the case history and the organization audit log. The system
states the limits of the evidence (no or partial recording, camera offline or removed) instead of
presenting a case as complete.

Not built here: the AI investigation summary, policy retrieval, recommendations and the approval
checkpoint (page 6 items 9–14) need the LangGraph agent and the RAG service, which do not exist yet.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from src.domain.entities.incident import (
    Incident,
    IncidentDetail,
    IncidentLocation,
    IncidentMessage,
    IncidentPage,
    IncidentPriority,
    IncidentStatus,
    StaffMember,
    check_transition,
    clean_description,
    clean_note,
    clean_title,
    note_required,
)
from src.domain.entities.location import LocationSelection
from src.domain.entities.user import User
from src.domain.exceptions.domain_errors import (
    Conflict,
    InvalidStateTransition,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)
from src.domain.repositories.i_audit_log_repo import IOrganizationAuditLog
from src.domain.repositories.i_incident_repo import IIncidentRepository, IncidentFilter, NewIncident
from src.domain.repositories.i_location_repo import ILocationRepository
from src.domain.repositories.i_unit_of_work import ITransaction
from src.domain.value_objects.enums import UserRole

# Around the moment of a case or an alert, the recording that should exist (page 6 item 19).
INCIDENT_WINDOW = timedelta(seconds=60)
ALERT_WINDOW = timedelta(seconds=15)
MAX_MESSAGE = 2000


def _staff(actor: User) -> None:
    if actor.role not in (UserRole.ADMIN, UserRole.SUPERVISOR):
        raise PermissionDenied("Only Admins and Supervisors can manage incidents.", code="STAFF_ONLY")


def _missing() -> NotFound:
    # The same answer for a case of another campus as for one that does not exist.
    return NotFound("Incident not found.", code="INCIDENT_NOT_FOUND")


def _closed() -> InvalidStateTransition:
    return InvalidStateTransition(
        "This case is resolved or dismissed. Reopen it for investigation first.", code="INCIDENT_CLOSED"
    )


@dataclass(frozen=True, slots=True)
class IncidentDetails:
    title: str
    description: str = ""
    priority: IncidentPriority = IncidentPriority.STANDARD
    occurred_at: datetime | None = None
    camera_id: UUID | None = None
    location: LocationSelection | None = None


class IncidentService:
    def __init__(
        self,
        incidents: IIncidentRepository,
        locations: ILocationRepository,
        audit: IOrganizationAuditLog,
        transaction: ITransaction,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repo = incidents
        self._locations = locations
        self._audit = audit
        self._transaction = transaction
        self._clock = clock

    # --- Helpers ------------------------------------------------------------------------------------------

    async def _load(self, incident_id: UUID, *, lock: bool = False) -> Incident:
        incident = await self._repo.get(incident_id, lock=lock)
        if incident is None:
            raise _missing()
        return incident

    async def _open(self, incident_id: UUID) -> Incident:
        incident = await self._load(incident_id, lock=True)
        if incident.status.is_closed:
            raise _closed()
        return incident

    async def _record(self, actor: User, incident: Incident, action: str, details: dict[str, Any]) -> None:
        await self._audit.record(actor, action, "INCIDENT", incident.id, {"reference": incident.reference, **details})

    async def _location(self, selection: LocationSelection) -> IncidentLocation:
        building = await self._locations.get_building(selection.building_id)
        if building is None:
            raise NotFound("Building or perimeter not found.", code="LOCATION_NOT_FOUND")
        floor = None
        if selection.floor_id is not None:
            floor = await self._locations.get_floor(selection.floor_id)
            if floor is None or floor.building_id != building.id:
                raise ValidationFailed("That floor belongs to another building.", code="INVALID_LOCATION")
        area = None
        if selection.area_id is not None:
            area = await self._locations.get_area(selection.area_id)
            if area is None or area.building_id != building.id or area.floor_id != (floor.id if floor else None):
                raise ValidationFailed("That area is not on the selected building and floor.", code="INVALID_LOCATION")
        return IncidentLocation(
            building.id,
            floor.id if floor else None,
            area.id if area else None,
            building.name,
            floor.name if floor else None,
            area.name if area else None,
        )

    async def _place(
        self, camera_id: UUID | None, selection: LocationSelection | None
    ) -> tuple[UUID | None, str | None, IncidentLocation]:
        """The camera (if any) and the location: the chosen one, else the camera's."""
        camera_name: str | None = None
        location = IncidentLocation()
        if camera_id is not None:
            camera = await self._repo.camera(camera_id)
            if camera is None:
                raise NotFound("Camera not found.", code="CAMERA_NOT_FOUND")
            camera_name, location = camera.camera_name, camera.location
        if selection is not None:
            location = await self._location(selection)
        return camera_id, camera_name, location

    async def _assignee(self, user_id: UUID | None) -> StaffMember | None:
        if user_id is None:
            return None
        member = await self._repo.staff_member(user_id)
        if member is None:
            raise ValidationFailed(
                "A case can be assigned only to an active Admin or Supervisor of this campus.", code="INVALID_ASSIGNEE"
            )
        return member

    def _occurred(self, value: datetime | None) -> datetime:
        now = self._clock()
        when = value or now
        if when.tzinfo is None:
            raise ValidationFailed("Give the time with its time zone.", code="INVALID_TIME")
        if when > now + timedelta(minutes=5):
            raise ValidationFailed("The incident cannot be in the future.", code="INVALID_TIME")
        return when

    # --- Reading ------------------------------------------------------------------------------------------

    async def search(self, actor: User, where: IncidentFilter) -> IncidentPage:
        _staff(actor)
        if where.since and where.until and where.since > where.until:
            raise ValidationFailed("The start of the period must be before its end.", code="INVALID_PERIOD")
        return await self._repo.page(where)

    async def assignable_staff(self, actor: User) -> list[StaffMember]:
        _staff(actor)
        return await self._repo.assignable_staff()

    async def of_alert(self, actor: User, alert_id: UUID) -> list[Incident]:
        _staff(actor)
        return await self._repo.incidents_of_alert(alert_id)

    async def detail(self, actor: User, incident_id: UUID) -> IncidentDetail:
        _staff(actor)
        incident = await self._load(incident_id)
        alerts = await self._repo.alerts(incident_id)
        windows: list[tuple[UUID | None, str, datetime, datetime]] = []
        if incident.camera_name is not None:
            windows.append(
                (
                    incident.camera_id,
                    incident.camera_name,
                    incident.occurred_at - INCIDENT_WINDOW,
                    incident.occurred_at + INCIDENT_WINDOW,
                )
            )
        for alert in alerts:
            windows.append(
                (
                    alert.camera_id,
                    alert.camera_name,
                    alert.detected_at - ALERT_WINDOW,
                    alert.last_detected_at + ALERT_WINDOW,
                )
            )
        return IncidentDetail(
            incident=incident,
            alerts=alerts,
            reports=await self._repo.reports(incident_id),
            evidence=await self._repo.evidence(incident_id),
            participants=await self._repo.participants(incident_id),
            gaps=await self._repo.footage_gaps(windows),
            history=await self._repo.history(incident_id),
            person_search=await self._repo.person_search_links(incident_id),
        )

    # --- Opening and changing a case ---------------------------------------------------------------------

    async def create(
        self,
        actor: User,
        details: IncidentDetails,
        *,
        assigned_to: UUID | None = None,
        alert_ids: list[UUID] | None = None,
        report_ids: list[UUID] | None = None,
        evidence_ids: list[UUID] | None = None,
    ) -> Incident:
        _staff(actor)
        title = clean_title(details.title)
        description = clean_description(details.description)
        occurred_at = self._occurred(details.occurred_at)
        camera_id, camera_name, location = await self._place(details.camera_id, details.location)
        assignee = await self._assignee(assigned_to)
        incident = await self._repo.create(
            NewIncident(
                id=uuid4(),
                title=title,
                description=description,
                priority=details.priority.value,
                occurred_at=occurred_at,
                camera_id=camera_id,
                camera_name=camera_name,
                location=location,
                assigned_to=assignee.user_id if assignee else None,
                assigned_name=assignee.full_name if assignee else None,
                created_by=actor.id,
                created_by_name=actor.full_name,
            )
        )
        await self._repo.add_event(
            incident.id, "CREATED", to_status=incident.status.value, details={"title": title}
        )
        await self._record(actor, incident, "INCIDENT_CREATED", {"title": title, "priority": incident.priority.value})
        for alert_id in dict.fromkeys(alert_ids or []):
            await self._link_alert(actor, incident, alert_id)
        for report_id in dict.fromkeys(report_ids or []):
            await self._link_report(actor, incident, report_id)
        for evidence_id in dict.fromkeys(evidence_ids or []):
            await self._link_evidence(actor, incident, evidence_id)
        if assignee is not None:
            await self._repo.add_event(incident.id, "ASSIGNED", details={"assignee": assignee.full_name})
            await self._notify_assignee(actor, incident, assignee.user_id)
        created = await self._load(incident.id)
        await self._transaction.commit()
        return created

    async def update(self, actor: User, incident_id: UUID, details: IncidentDetails) -> Incident:
        _staff(actor)
        current = await self._open(incident_id)
        camera_id, camera_name, location = await self._place(details.camera_id, details.location)
        wanted: dict[str, Any] = {
            "title": clean_title(details.title),
            "description": clean_description(details.description),
            "priority": details.priority.value,
            "occurred_at": self._occurred(details.occurred_at or current.occurred_at),
            "camera_id": camera_id,
            "camera_name": camera_name,
            "building_id": location.building_id,
            "floor_id": location.floor_id,
            "area_id": location.area_id,
            "building_name": location.building_name,
            "floor_name": location.floor_name,
            "area_name": location.area_name,
        }
        before: dict[str, Any] = {
            "title": current.title,
            "description": current.description,
            "priority": current.priority.value,
            "occurred_at": current.occurred_at,
            "camera_id": current.camera_id,
            "camera_name": current.camera_name,
            "building_id": current.location.building_id,
            "floor_id": current.location.floor_id,
            "area_id": current.location.area_id,
            "building_name": current.location.building_name,
            "floor_name": current.location.floor_name,
            "area_name": current.location.area_name,
        }
        changes = {k: v for k, v in wanted.items() if before[k] != v}
        if not changes:
            return current
        await self._repo.update_details(incident_id, changes)
        changed = sorted({k.removesuffix("_id").removesuffix("_name") for k in changes})
        await self._repo.add_event(incident_id, "DETAILS_UPDATED", details={"changed": changed})
        await self._record(actor, current, "INCIDENT_UPDATED", {"changed": changed})
        updated = await self._load(incident_id)
        await self._transaction.commit()
        return updated

    async def change_status(self, actor: User, incident_id: UUID, status: IncidentStatus, note: str | None) -> Incident:
        _staff(actor)
        current = await self._load(incident_id, lock=True)
        check_transition(current.status, status)
        text = clean_note(note, required=note_required(current.status, status))
        await self._repo.set_status(incident_id, status.value, text, status.is_closed)
        await self._repo.add_event(
            incident_id, "STATUS_CHANGED", from_status=current.status.value, to_status=status.value, note=text
        )
        await self._record(
            actor, current, "INCIDENT_STATUS_CHANGED", {"from": current.status.value, "to": status.value}
        )
        title = f"{current.reference} is now {status.value.replace('_', ' ').lower()}: {current.title}"
        for user_id in {current.assigned_to, current.created_by} - {None, actor.id}:
            assert user_id is not None
            if await self._repo.staff_member(user_id) is not None:
                await self._repo.notify(user_id, "INCIDENT_STATUS_CHANGED", incident_id, title)
        updated = await self._load(incident_id)
        await self._transaction.commit()
        return updated

    async def _notify_assignee(self, actor: User, incident: Incident, user_id: UUID) -> None:
        if user_id != actor.id:
            title = f"{incident.reference} was assigned to you: {incident.title}"
            await self._repo.notify(user_id, "INCIDENT_ASSIGNED", incident.id, title)

    async def assign(self, actor: User, incident_id: UUID, user_id: UUID | None) -> Incident:
        _staff(actor)
        current = await self._open(incident_id)
        if current.assigned_to == user_id:
            return current
        assignee = await self._assignee(user_id)
        await self._repo.set_assignee(incident_id, user_id, assignee.full_name if assignee else None)
        if assignee is None:
            await self._repo.add_event(incident_id, "UNASSIGNED", details={"previous": current.assigned_name})
        else:
            await self._repo.add_event(incident_id, "ASSIGNED", details={"assignee": assignee.full_name})
            await self._notify_assignee(actor, current, assignee.user_id)
        await self._record(
            actor,
            current,
            "INCIDENT_ASSIGNED",
            {"assignee": assignee.full_name if assignee else None, "assignee_id": str(user_id) if user_id else None},
        )
        updated = await self._load(incident_id)
        await self._transaction.commit()
        return updated

    async def add_note(self, actor: User, incident_id: UUID, note: str) -> None:
        _staff(actor)
        incident = await self._load(incident_id, lock=True)
        text = clean_note(note, required=True)
        await self._repo.add_event(incident_id, "NOTE_ADDED", note=text)
        await self._repo.touch(incident_id)
        await self._record(actor, incident, "INCIDENT_NOTE_ADDED", {"length": len(text or "")})
        await self._transaction.commit()

    # --- Links --------------------------------------------------------------------------------------------

    async def _link_alert(self, actor: User, incident: Incident, alert_id: UUID) -> None:
        if not await self._repo.alert_exists(alert_id):
            raise NotFound("Alert not found.", code="ALERT_NOT_FOUND")
        if await self._repo.link_alert(incident.id, alert_id, actor.full_name):
            await self._repo.add_event(incident.id, "ALERT_LINKED", details={"alert_id": str(alert_id)})
            await self._record(actor, incident, "INCIDENT_ALERT_LINKED", {"alert_id": str(alert_id)})

    async def _link_report(self, actor: User, incident: Incident, report_id: UUID) -> None:
        found, current = await self._repo.report_incident(report_id)
        if not found:
            raise NotFound("Report not found.", code="REPORT_NOT_FOUND")
        if current == incident.id:
            return
        if current is not None:
            raise Conflict("This report is already linked to another case.", code="REPORT_ALREADY_LINKED")
        await self._repo.set_report_incident(report_id, incident.id)
        await self._repo.add_event(incident.id, "REPORT_LINKED", details={"report_id": str(report_id)})
        await self._record(actor, incident, "INCIDENT_REPORT_LINKED", {"report_id": str(report_id)})

    async def _link_evidence(self, actor: User, incident: Incident, evidence_id: UUID) -> None:
        found, current = await self._repo.evidence_incident(evidence_id)
        if not found:
            raise NotFound("Evidence not found.", code="EVIDENCE_NOT_FOUND")
        if current == incident.id:
            return
        if current is not None:
            raise Conflict("This evidence is already linked to another case.", code="EVIDENCE_ALREADY_LINKED")
        await self._repo.set_evidence_incident(evidence_id, incident.id)
        await self._repo.add_event(incident.id, "EVIDENCE_LINKED", details={"evidence_id": str(evidence_id)})
        await self._record(actor, incident, "INCIDENT_EVIDENCE_LINKED", {"evidence_id": str(evidence_id)})

    async def attach_person_search(self, actor: User, incident_id: UUID, candidate: Any) -> str:
        """Records a verified Person Search result in an open case; the caller links and commits (D-024)."""
        _staff(actor)
        incident = await self._open(incident_id)
        await self._repo.add_event(
            incident_id,
            "PERSON_SEARCH_LINKED",
            details={
                "result_id": str(candidate.id),
                "camera": candidate.camera_name,
                "observed_at": candidate.observed_at.isoformat(),
                "similarity": round(candidate.similarity, 4),
                "band": candidate.band.value,
            },
        )
        await self._repo.touch(incident_id)
        return incident.reference

    async def attach_evidence(self, actor: User, incident_id: UUID, evidence_id: UUID) -> None:
        """Links evidence being saved to an open case; the caller commits (evidence vault, decision D-024)."""
        _staff(actor)
        incident = await self._open(incident_id)
        await self._link_evidence(actor, incident, evidence_id)
        await self._repo.touch(incident_id)

    async def link(self, actor: User, incident_id: UUID, kind: str, target_id: UUID) -> Incident:
        _staff(actor)
        incident = await self._open(incident_id)
        linker = {"alert": self._link_alert, "report": self._link_report, "evidence": self._link_evidence}[kind]
        await linker(actor, incident, target_id)
        await self._repo.touch(incident_id)
        updated = await self._load(incident_id)
        await self._transaction.commit()
        return updated

    async def unlink(self, actor: User, incident_id: UUID, kind: str, target_id: UUID) -> Incident:
        _staff(actor)
        incident = await self._open(incident_id)
        if kind == "alert":
            removed = await self._repo.unlink_alert(incident_id, target_id)
        elif kind == "report":
            _, current = await self._repo.report_incident(target_id)
            removed = current == incident_id
            if removed:
                await self._repo.set_report_incident(target_id, None)
        else:
            _, current = await self._repo.evidence_incident(target_id)
            removed = current == incident_id
            if removed:
                await self._repo.set_evidence_incident(target_id, None)
        if not removed:
            raise NotFound(f"This {kind} is not linked to the case.", code="NOT_LINKED")
        event = {"alert": "ALERT_UNLINKED", "report": "REPORT_UNLINKED", "evidence": "EVIDENCE_UNLINKED"}[kind]
        await self._repo.add_event(incident_id, event, details={f"{kind}_id": str(target_id)})
        await self._repo.touch(incident_id)
        await self._record(actor, incident, f"INCIDENT_{kind.upper()}_UNLINKED", {f"{kind}_id": str(target_id)})
        updated = await self._load(incident_id)
        await self._transaction.commit()
        return updated

    # --- Chat participants and messages ---------------------------------------------------------------------

    async def add_participant(self, actor: User, incident_id: UUID, guard_user_id: UUID) -> None:
        _staff(actor)
        incident = await self._open(incident_id)
        guard = await self._repo.guard(guard_user_id)
        if guard is None:
            raise NotFound("Guard not found.", code="GUARD_NOT_FOUND")
        if await self._repo.add_participant(incident_id, guard_user_id):
            await self._repo.add_event(incident_id, "PARTICIPANT_ADDED", details={"guard": guard.display_id})
            await self._record(actor, incident, "INCIDENT_PARTICIPANT_ADDED", {"guard": guard.display_id})
            title = f"You were added to the chat of {incident.reference}: {incident.title}"
            await self._repo.notify(guard_user_id, "INCIDENT_INVITED", incident_id, title)
        await self._transaction.commit()

    async def remove_participant(self, actor: User, incident_id: UUID, guard_user_id: UUID) -> None:
        _staff(actor)
        incident = await self._load(incident_id, lock=True)
        if not await self._repo.remove_participant(incident_id, guard_user_id):
            raise NotFound("This Guard was not invited to the case.", code="NOT_A_PARTICIPANT")
        guard = await self._repo.guard(guard_user_id)
        label = guard.display_id if guard else str(guard_user_id)
        await self._repo.add_event(incident_id, "PARTICIPANT_REMOVED", details={"guard": label})
        await self._record(actor, incident, "INCIDENT_PARTICIPANT_REMOVED", {"guard": label})
        await self._transaction.commit()

    async def messages(
        self, actor: User, incident_id: UUID, *, after: datetime | None = None, limit: int = 200
    ) -> list[IncidentMessage]:
        """Staff of the campus, or a Guard taking part (Row-Level Security hides other cases)."""
        await self._load(incident_id)
        return await self._repo.messages(incident_id, after=after, limit=max(1, min(limit, 500)))

    async def post_message(self, actor: User, incident_id: UUID, body: str) -> IncidentMessage:
        incident = await self._load(incident_id)
        if incident.status.is_closed:
            raise _closed()
        text = body.strip()
        if not 1 <= len(text) <= MAX_MESSAGE:
            raise ValidationFailed(f"A message must be 1 to {MAX_MESSAGE} characters.", code="INVALID_MESSAGE")
        message = await self._repo.post_message(incident_id, actor.full_name, text)
        await self._record(actor, incident, "INCIDENT_MESSAGE_POSTED", {"message_id": str(message.id)})
        await self._transaction.commit()
        return message

    async def guard_incidents(self, actor: User) -> list[Incident]:
        if actor.role is not UserRole.GUARD:
            raise PermissionDenied("Only Guards use this list.", code="GUARD_ONLY")
        return await self._repo.participant_incidents()
