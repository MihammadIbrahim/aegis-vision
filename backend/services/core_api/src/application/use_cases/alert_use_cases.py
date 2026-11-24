"""Alert Management (requirements Section 15; decision D-013).

* **Creation:** only from a detection sent by an AI worker to the detection intake. Repeats of an
  open alert are merged, and the same detection sent twice is ignored.
* **Routing:** to the active Guards of the camera's campus whose assigned building, floor or area
  contains the camera's location. Staff (Admins and Supervisors) of the campus see every alert.
* **Guards:** a recipient Guard acknowledges, responds, and reports an assessment. An assessment
  never closes the alert (15.3).
* **Staff:** resolve, dismiss (with a reason) and escalate within the campus.
* **Offline:** every action may carry a ``client_action_id``; sending it again changes nothing.

Another campus's alert, or an alert not sent to the Guard, answers 404.
"""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID, uuid4

from src.application.use_cases.location_use_cases import LocationResolver
from src.domain.entities.alert import (
    Alert,
    AlertEscalation,
    AlertEvent,
    AlertMessage,
    AlertStatus,
    AlertType,
    EscalationScope,
    GuardAction,
)
from src.domain.entities.alert_escalation import EscalationRequest
from src.domain.entities.incident import StaffMember
from src.domain.entities.user import User
from src.domain.exceptions.domain_errors import (
    Conflict,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)
from src.domain.repositories.i_alert_repo import (
    AlertFilter,
    Detection,
    IAlertRepository,
)
from src.domain.repositories.i_audit_log_repo import IOrganizationAuditLog
from src.domain.repositories.i_location_repo import ILocationRepository
from src.domain.repositories.i_unit_of_work import ITransaction
from src.domain.value_objects.enums import UserRole

MAX_NOTE = 1000
MAX_MESSAGE = 2000


def _missing() -> NotFound:
    return NotFound("Alert not found.", code="ALERT_NOT_FOUND")


def _closed(status: AlertStatus) -> Conflict:
    return Conflict(f"This alert is already {status.value.lower()}.", code="ALERT_CLOSED")


def _ensure_staff(actor: User) -> None:
    if actor.role not in (UserRole.ADMIN, UserRole.SUPERVISOR):
        raise PermissionDenied("Only Admins and Supervisors can do this.", code="STAFF_ONLY")


def _text(value: str | None, limit: int, what: str, *, required: bool = False) -> str | None:
    cleaned = (value or "").strip()
    if not cleaned:
        if required:
            raise ValidationFailed(f"{what} is required.", code="INVALID_TEXT")
        return None
    if len(cleaned) > limit:
        raise ValidationFailed(f"{what} must be at most {limit} characters.", code="INVALID_TEXT")
    return cleaned


# --- Detection intake (AI workers) ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IngestResult:
    alert_id: UUID
    outcome: str  # CREATED, REPEATED (merged into an open alert) or DUPLICATE (already received)


class IngestDetection:
    def __init__(self, alerts: IAlertRepository, transaction: ITransaction, *, dedup_seconds: int) -> None:
        self._alerts = alerts
        self._transaction = transaction
        self._dedup = dedup_seconds

    async def execute(self, detection: Detection) -> IngestResult:
        AlertType(detection.alert_type)  # validated again by the database
        alert_id, outcome = await self._alerts.ingest(detection, dedup_seconds=self._dedup)
        if alert_id is None:
            await self._transaction.rollback()
            if outcome == "CAMERA_NOT_FOUND":
                raise NotFound("Camera not found or its organization is not active.", code="CAMERA_NOT_FOUND")
            raise ValidationFailed("Unknown detection type.", code="INVALID_TYPE")
        await self._transaction.commit()
        return IngestResult(alert_id, outcome)


# --- Reading -------------------------------------------------------------------------------------------


class ViewAlerts:
    """Staff see their campus's alerts; a Guard sees only alerts sent to them (database rule)."""

    def __init__(self, alerts: IAlertRepository) -> None:
        self._alerts = alerts

    async def list_alerts(self, actor: User, where: AlertFilter) -> list[Alert]:
        return await self._alerts.list_alerts(where)

    async def get(self, actor: User, alert_id: UUID) -> Alert:
        alert = await self._alerts.get(alert_id)
        if alert is None:
            raise _missing()
        return alert

    async def history(self, actor: User, alert_id: UUID) -> list[AlertEvent]:
        await self.get(actor, alert_id)
        return await self._alerts.history(alert_id)


# --- Guard actions -------------------------------------------------------------------------------------


_GUARD_OUTCOMES = {
    "NOT_FOUND": lambda s: _missing(),
    "ALERT_CLOSED": lambda s: _closed(AlertStatus(s)),
    "ACKNOWLEDGE_FIRST": lambda s: Conflict("Acknowledge the alert first.", code="ACKNOWLEDGE_FIRST"),
    "INVALID_ACTION": lambda s: ValidationFailed("Unknown action.", code="INVALID_ACTION"),
}


@dataclass(frozen=True, slots=True)
class ActionResult:
    outcome: str  # DONE, UNCHANGED or REPLAYED
    status: AlertStatus


class GuardAlertAction:
    def __init__(self, alerts: IAlertRepository, transaction: ITransaction) -> None:
        self._alerts = alerts
        self._transaction = transaction

    async def execute(
        self, actor: User, alert_id: UUID, action: GuardAction, client_action_id: UUID | None
    ) -> ActionResult:
        if actor.role is not UserRole.GUARD:
            raise PermissionDenied("Only Guards the alert was sent to can do this.", code="GUARD_ONLY")
        outcome, status = await self._alerts.guard_action(alert_id, action.value, client_action_id)
        if outcome in _GUARD_OUTCOMES:
            await self._transaction.rollback()
            raise _GUARD_OUTCOMES[outcome](status)
        await self._transaction.commit()
        return ActionResult(outcome, AlertStatus(status or "NEW"))


# --- Staff actions ------------------------------------------------------------------------------------


class _StaffAction:
    def __init__(self, alerts: IAlertRepository, audit: IOrganizationAuditLog, transaction: ITransaction) -> None:
        self._alerts = alerts
        self._audit = audit
        self._transaction = transaction

    async def _open_alert(self, actor: User, alert_id: UUID, client_action_id: UUID | None) -> Alert | None:
        """The locked alert, or None when this exact action was already applied (a resend)."""
        _ensure_staff(actor)
        alert = await self._alerts.get(alert_id, lock=True)
        if alert is None:
            raise _missing()
        if await self._alerts.action_replayed(actor.id, client_action_id):
            return None
        if alert.status.is_closed:
            raise _closed(alert.status)
        return alert

    async def _finish(self, alert_id: UUID) -> Alert:
        alert = await self._alerts.get(alert_id)
        assert alert is not None
        await self._transaction.commit()
        return alert


class CloseAlert(_StaffAction):
    """Resolve (optional note) or dismiss (reason required). Both are final."""

    async def execute(
        self, actor: User, alert_id: UUID, status: AlertStatus, note: str | None, client_action_id: UUID | None
    ) -> Alert:
        assert status in (AlertStatus.RESOLVED, AlertStatus.DISMISSED)
        dismissing = status is AlertStatus.DISMISSED
        cleaned = _text(note, MAX_NOTE, "A reason" if dismissing else "The note", required=dismissing)
        alert = await self._open_alert(actor, alert_id, client_action_id)
        if alert is None:
            return await self._finish(alert_id)
        await self._alerts.close(alert.id, status, actor.id, cleaned)
        await self._alerts.add_event(
            alert,
            actor.id,
            actor.role.value,
            status.value,
            status.value,
            {"note_given": cleaned is not None},
            client_action_id,
        )
        kind = "ALERT_DISMISSED" if dismissing else "ALERT_RESOLVED"
        guards = [r.guard_user_id for r in alert.recipients]
        await self._alerts.notify(alert, guards, kind, f"Alert {status.value.lower()}: {alert.camera_name}")
        await self._audit.record(actor, f"ALERT_{status.value}", "ALERT", alert.id, {"from_status": alert.status.value})
        return await self._finish(alert.id)


class EscalateAlert(_StaffAction):
    """Send the alert to more Guards of the same campus (15.4): all of them, those assigned at or
    below a location, or chosen Guards. Never to another campus."""

    def __init__(
        self,
        alerts: IAlertRepository,
        audit: IOrganizationAuditLog,
        transaction: ITransaction,
        locations: ILocationRepository,
    ) -> None:
        super().__init__(alerts, audit, transaction)
        self._resolver = LocationResolver(locations, transaction)

    async def execute(
        self, actor: User, alert_id: UUID, request: EscalationRequest, client_action_id: UUID | None
    ) -> tuple[Alert, int]:
        note = _text(request.note, MAX_NOTE, "The note")
        if request.scope is EscalationScope.LOCATION and request.location is None:
            raise ValidationFailed("Choose the location to escalate to.", code="LOCATION_REQUIRED")
        if request.scope is EscalationScope.GUARDS and not request.guard_ids:
            raise ValidationFailed("Choose at least one Guard.", code="GUARDS_REQUIRED")
        alert = await self._open_alert(actor, alert_id, client_action_id)
        if alert is None:
            return await self._finish(alert_id), 0
        target = (
            await self._resolver.resolve(request.location)
            if request.scope is EscalationScope.LOCATION and request.location is not None
            else None
        )
        guard_ids = [g.strip() for g in request.guard_ids] if request.scope is EscalationScope.GUARDS else None
        guards = await self._alerts.eligible_guards(alert, location=target, guard_ids=guard_ids)
        if not guards:
            raise ValidationFailed(
                "No other active Guard of this campus matches; nobody was added.", code="NO_ELIGIBLE_GUARDS"
            )
        escalation_id = uuid4()
        await self._alerts.add_escalation(alert, escalation_id, actor.id, request.scope.value, target, note, guards)
        if alert.status is not AlertStatus.ESCALATED:
            await self._alerts.set_status(alert.id, AlertStatus.ESCALATED)
        await self._alerts.add_event(
            alert,
            actor.id,
            actor.role.value,
            "ESCALATED",
            AlertStatus.ESCALATED.value,
            {"scope": request.scope.value, "added_guards": len(guards), "escalation_id": str(escalation_id)},
            client_action_id,
        )
        await self._alerts.notify(alert, guards, "ALERT_ESCALATED", f"Assistance needed: {alert.camera_name}")
        await self._audit.record(
            actor, "ALERT_ESCALATED", "ALERT", alert.id, {"scope": request.scope.value, "added_guards": len(guards)}
        )
        return await self._finish(alert.id), len(guards)


# --- Alert chat ------------------------------------------------------------------------------------------


class AlertChat:
    """Messages between the campus's staff and the alert's Guards (15.5). Nobody else can read them."""

    def __init__(self, alerts: IAlertRepository, audit: IOrganizationAuditLog, transaction: ITransaction) -> None:
        self._alerts = alerts
        self._audit = audit
        self._transaction = transaction

    async def list_messages(self, actor: User, alert_id: UUID) -> list[AlertMessage]:
        if await self._alerts.get(alert_id) is None:
            raise _missing()
        return await self._alerts.messages(alert_id)

    async def post(self, actor: User, alert_id: UUID, body: str, client_message_id: UUID | None) -> list[AlertMessage]:
        cleaned = _text(body, MAX_MESSAGE, "The message", required=True)
        assert cleaned is not None
        alert = await self._alerts.get(alert_id)
        if alert is None:
            raise _missing()
        if alert.status.is_closed:
            raise _closed(alert.status)
        message_id, created = await self._alerts.post_message(
            alert, uuid4(), actor.id, actor.role.value, actor.full_name, cleaned, client_message_id
        )
        if created:  # who wrote, where and when; never the text
            await self._audit.record(actor, "ALERT_MESSAGE_POSTED", "ALERT", alert.id, {"message_id": str(message_id)})
        messages = await self._alerts.messages(alert_id)
        await self._transaction.commit()
        return messages


# --- Responsible staff and escalation history (decision D-024) ------------------------------------------


class IAlertOperations(Protocol):
    async def escalations(self, alert_id: UUID) -> list[AlertEscalation]: ...

    async def set_handler(self, alert_id: UUID, user_id: UUID | None, name: str | None) -> None: ...


class IStaffDirectory(Protocol):
    async def staff_member(self, user_id: UUID) -> StaffMember | None: ...


class AlertHandling(_StaffAction):
    """Who handles an alert (page 4 item 12) and its escalations (items 21, 25, 26). Staff only."""

    def __init__(
        self,
        alerts: IAlertRepository,
        audit: IOrganizationAuditLog,
        transaction: ITransaction,
        operations: IAlertOperations,
        staff: IStaffDirectory,
    ) -> None:
        super().__init__(alerts, audit, transaction)
        self._operations = operations
        self._staff = staff

    async def escalations(self, actor: User, alert_id: UUID) -> list[AlertEscalation]:
        _ensure_staff(actor)
        if await self._alerts.get(alert_id) is None:
            raise _missing()
        return await self._operations.escalations(alert_id)

    async def assign_handler(self, actor: User, alert_id: UUID, user_id: UUID | None) -> Alert:
        alert = await self._open_alert(actor, alert_id, None)
        assert alert is not None
        member = None
        if user_id is not None:
            member = await self._staff.staff_member(user_id)
            if member is None:
                raise ValidationFailed(
                    "Only an active Admin or Supervisor of this campus can handle an alert.", code="INVALID_HANDLER"
                )
        if alert.handled_by == user_id:
            return await self._finish(alert.id)
        await self._operations.set_handler(alert.id, user_id, member.full_name if member else None)
        await self._alerts.add_event(
            alert,
            actor.id,
            actor.role.value,
            "HANDLER_ASSIGNED",
            alert.status.value,
            {"handler": member.full_name if member else None},
            None,
        )
        if member is not None and member.user_id != actor.id:
            await self._alerts.notify(
                alert, [member.user_id], "ALERT_HANDLER_ASSIGNED", f"You are handling the alert: {alert.camera_name}"
            )
        await self._audit.record(
            actor, "ALERT_HANDLER_ASSIGNED", "ALERT", alert.id, {"handler": member.full_name if member else None}
        )
        return await self._finish(alert.id)
