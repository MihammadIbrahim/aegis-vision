"""In-app voice calls about an alert or a case (decision D-025, migration 0030).

A call is a LiveKit audio room. The API records who started, was invited to, joined, declined, left or missed it and
signs short-lived room tokens; no audio passes through the API or the database.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class CallScope(StrEnum):
    ALERT = "ALERT"
    INCIDENT = "INCIDENT"


class CallStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ENDED = "ENDED"


class ParticipantState(StrEnum):
    INVITED = "INVITED"  # ringing on their device
    JOINED = "JOINED"
    DECLINED = "DECLINED"
    LEFT = "LEFT"
    MISSED = "MISSED"  # the call ended before they answered


class EndReason(StrEnum):
    ENDED_BY_USER = "ENDED_BY_USER"
    EVERYONE_LEFT = "EVERYONE_LEFT"
    TARGET_CLOSED = "TARGET_CLOSED"  # the alert or case was resolved or dismissed


@dataclass(frozen=True, slots=True)
class CallTarget:
    """The alert or case a call is about (exactly one is set)."""

    scope: CallScope
    alert_id: UUID | None = None
    incident_id: UUID | None = None

    @property
    def target_id(self) -> UUID:
        found = self.alert_id if self.scope is CallScope.ALERT else self.incident_id
        assert found is not None
        return found

    @property
    def audit_prefix(self) -> str:
        return "ALERT_CALL" if self.scope is CallScope.ALERT else "INCIDENT_CALL"


@dataclass(frozen=True, slots=True)
class CallParticipant:
    user_id: UUID
    full_name: str
    role: str
    state: ParticipantState
    invited_at: datetime
    joined_at: datetime | None
    left_at: datetime | None


@dataclass(frozen=True, slots=True)
class CallSession:
    id: UUID
    target: CallTarget
    room_name: str
    status: CallStatus
    started_by: UUID | None
    started_by_name: str | None
    started_by_role: str
    started_at: datetime
    ended_at: datetime | None
    end_reason: EndReason | None
    participants: list[CallParticipant] = field(default_factory=list)
    # What the call is about, for incoming-call banners (decision D-026): "Weapon alert · Gate Camera" or
    # "INC-000007 · Weapon at the lobby", and "Admin Block → Ground → Lobby".
    target_title: str = ""
    target_location: str | None = None

    def participant(self, user_id: UUID) -> CallParticipant | None:
        return next((p for p in self.participants if p.user_id == user_id), None)

    @property
    def joined_count(self) -> int:
        return sum(1 for p in self.participants if p.state is ParticipantState.JOINED)


def room_name_for(call_id: UUID) -> str:
    """The LiveKit room of a call: random, and without any organization, campus or person information."""
    return f"cctv-call-{call_id}"
