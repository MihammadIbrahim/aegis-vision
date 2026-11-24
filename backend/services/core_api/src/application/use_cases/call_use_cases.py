"""In-app voice calls about an alert or a case (decision D-025). Admins, Supervisors and Guards use the same rules:

* Who: anyone who may see the alert or case (staff of its campus; a Guard recipient of the alert; a Guard taking part
  in the case). Other campuses' alerts and cases answer 404; Super Admin tokens never reach here (403).
* Start: on an open alert or case. When a call is already active on it, starting joins that call instead. A new call
  rings the campus's active Admins and Supervisors and the target's Guards; anyone allowed may also join uninvited.
* End: Admins, Supervisors and the person who started it end a call for everyone; others leave. A call also ends when
  the last participant leaves and when its alert or case is resolved or dismissed (database trigger).
* Audit: ``ALERT_CALL_*`` / ``INCIDENT_CALL_*`` records (started, joined, declined, left, ended), never a token.
"""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID, uuid4

from src.application.interfaces.i_voice_call_provider import IVoiceCallProvider, VoiceGrant
from src.domain.entities.call import (
    CallScope,
    CallSession,
    CallStatus,
    CallTarget,
    EndReason,
    ParticipantState,
    room_name_for,
)
from src.domain.entities.user import User
from src.domain.exceptions.domain_errors import Conflict, NotFound, PermissionDenied
from src.domain.repositories.i_unit_of_work import ITransaction
from src.domain.value_objects.enums import UserRole

_CLOSED = {"RESOLVED", "DISMISSED"}
_STAFF = (UserRole.ADMIN, UserRole.SUPERVISOR)


class ICallRepository(Protocol):
    async def target_status(self, target: CallTarget) -> str | None: ...
    async def invitees(self, target: CallTarget) -> list[UUID]: ...
    async def get(self, call_id: UUID) -> CallSession | None: ...
    async def active_for(self, target: CallTarget) -> CallSession | None: ...
    async def ringing_or_joined(self) -> list[CallSession]: ...
    async def create(self, call_id: UUID, room: str, target: CallTarget) -> bool: ...
    async def invite(self, call_id: UUID, user_ids: list[UUID]) -> None: ...
    async def join(self, call_id: UUID) -> None: ...
    async def set_my_state(self, call_id: UUID, state: ParticipantState) -> None: ...
    async def end(self, call_id: UUID, reason: EndReason) -> None: ...


class IAuditLog(Protocol):
    async def record(
        self, actor: User, action: str, target_type: str, target_id: UUID | None, metadata: dict[str, object]
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class JoinedCall:
    call: CallSession
    grant: VoiceGrant
    outcome: str  # STARTED or JOINED


def _not_found(target: CallTarget) -> NotFound:
    if target.scope is CallScope.ALERT:
        return NotFound("Alert not found.", code="ALERT_NOT_FOUND")
    return NotFound("Case not found.", code="INCIDENT_NOT_FOUND")


def _closed(target: CallTarget) -> Conflict:
    if target.scope is CallScope.ALERT:
        return Conflict("This alert is closed; calls can no longer be started or joined.", code="ALERT_CLOSED")
    return Conflict("This case is closed; calls can no longer be started or joined.", code="INCIDENT_CLOSED")


def _call_missing() -> NotFound:
    return NotFound("Call not found.", code="CALL_NOT_FOUND")


def _call_ended() -> Conflict:
    return Conflict("This call has ended.", code="CALL_ENDED")


class CallService:
    def __init__(
        self, calls: ICallRepository, audit: IAuditLog, transaction: ITransaction, voice: IVoiceCallProvider
    ) -> None:
        self._calls = calls
        self._audit = audit
        self._transaction = transaction
        self._voice = voice

    @property
    def available(self) -> bool:
        return self._voice.configured

    async def _open_target(self, target: CallTarget) -> None:
        status = await self._calls.target_status(target)
        if status is None:
            raise _not_found(target)
        if status in _CLOSED:
            raise _closed(target)

    async def _record(self, actor: User, call: CallSession, event: str, **extra: object) -> None:
        target = call.target
        await self._audit.record(
            actor,
            f"{target.audit_prefix}_{event}",
            target.scope.value,
            target.target_id,
            {"call_id": str(call.id), **extra},
        )

    def _grant(self, actor: User, call: CallSession) -> VoiceGrant:
        return self._voice.grant(
            room=call.room_name,
            identity=str(actor.id),
            name=actor.full_name,
            metadata={"role": actor.role.value, "call_id": str(call.id)},
        )

    async def _load(self, call_id: UUID) -> CallSession:
        call = await self._calls.get(call_id)
        if call is None:
            raise _call_missing()
        return call

    # --- Reading --------------------------------------------------------------------------------------------------

    async def active(self, actor: User, target: CallTarget) -> CallSession | None:
        if await self._calls.target_status(target) is None:
            raise _not_found(target)
        return await self._calls.active_for(target)

    async def get(self, actor: User, call_id: UUID) -> CallSession:
        return await self._load(call_id)

    async def mine(self, actor: User) -> list[CallSession]:
        return await self._calls.ringing_or_joined()

    # --- Starting and joining -------------------------------------------------------------------------------------

    async def start(self, actor: User, target: CallTarget) -> JoinedCall:
        await self._open_target(target)
        # Refused before anything is written, so a server without a provider keeps no half-started calls.
        self._voice.grant(room=room_name_for(uuid4()), identity=str(actor.id), name=actor.full_name, metadata={})
        existing = await self._calls.active_for(target)
        if existing is None:
            call_id = uuid4()
            if await self._calls.create(call_id, room_name_for(call_id), target):
                await self._calls.invite(call_id, await self._calls.invitees(target))
                call = await self._load(call_id)
                await self._record(actor, call, "STARTED", invited=len(call.participants) - 1)
                grant = self._grant(actor, call)
                await self._transaction.commit()
                return JoinedCall(call, grant, "STARTED")
            existing = await self._calls.active_for(target)  # someone else started one at the same moment
            if existing is None:
                raise Conflict("The call could not be started. Please try again.", code="CALL_START_CONFLICT")
        return await self._join(actor, existing)

    async def join(self, actor: User, call_id: UUID) -> JoinedCall:
        call = await self._load(call_id)
        if call.status is CallStatus.ENDED:
            raise _call_ended()
        await self._open_target(call.target)
        self._grant(actor, call)  # 503 VOICE_CALL_NOT_CONFIGURED before any change
        return await self._join(actor, call)

    async def _join(self, actor: User, call: CallSession) -> JoinedCall:
        mine = call.participant(actor.id)
        if mine is None or mine.state is not ParticipantState.JOINED:
            await self._calls.join(call.id)
            await self._record(actor, call, "JOINED")
        refreshed = await self._load(call.id)
        grant = self._grant(actor, refreshed)
        await self._transaction.commit()
        return JoinedCall(refreshed, grant, "JOINED")

    # --- Declining, leaving and ending ----------------------------------------------------------------------------

    async def decline(self, actor: User, call_id: UUID) -> CallSession:
        call = await self._load(call_id)
        mine = call.participant(actor.id)
        if call.status is CallStatus.ENDED or (mine is not None and mine.state is ParticipantState.DECLINED):
            return call  # nothing left to decline
        if mine is None or mine.state is not ParticipantState.INVITED:
            raise Conflict("Only a ringing invitation can be declined.", code="NOT_INVITED")
        await self._calls.set_my_state(call.id, ParticipantState.DECLINED)
        await self._record(actor, call, "DECLINED")
        refreshed = await self._load(call.id)
        await self._transaction.commit()
        return refreshed

    async def leave(self, actor: User, call_id: UUID) -> tuple[CallSession, bool]:
        """Returns the call and whether it ended because nobody is left in it."""
        call = await self._load(call_id)
        mine = call.participant(actor.id)
        if call.status is CallStatus.ENDED or mine is None or mine.state is not ParticipantState.JOINED:
            return call, False
        await self._calls.set_my_state(call.id, ParticipantState.LEFT)
        await self._record(actor, call, "LEFT")
        refreshed = await self._load(call.id)
        ended = refreshed.joined_count == 0
        if ended:
            await self._calls.end(call.id, EndReason.EVERYONE_LEFT)
            await self._record(actor, call, "ENDED", reason=EndReason.EVERYONE_LEFT.value)
            refreshed = await self._load(call.id)
        await self._transaction.commit()
        if ended:
            await self._voice.close_room(call.room_name)
        return refreshed, ended

    async def end(self, actor: User, call_id: UUID) -> CallSession:
        call = await self._load(call_id)
        if call.status is CallStatus.ENDED:
            return call
        if actor.role not in _STAFF and call.started_by != actor.id:
            raise PermissionDenied(
                "Only an Admin, a Supervisor or the person who started the call can end it for everyone. "
                "Leave the call instead.",
                code="CALL_END_NOT_ALLOWED",
            )
        await self._calls.end(call.id, EndReason.ENDED_BY_USER)
        await self._record(actor, call, "ENDED", reason=EndReason.ENDED_BY_USER.value)
        refreshed = await self._load(call.id)
        await self._transaction.commit()
        await self._voice.close_room(call.room_name)
        return refreshed
