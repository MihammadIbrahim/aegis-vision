"""Bodies for in-app voice calls, the responder roster, device push tokens and the real-time status (decision
D-025). Responses never contain a password, a push token of another user or a provider secret; a voice ``token`` is
returned only to the participant it was signed for."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.application.interfaces.i_voice_call_provider import VoiceGrant
from src.domain.entities.call import CallSession


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


CallScopeValue = Literal["ALERT", "INCIDENT"]
CallStatusValue = Literal["ACTIVE", "ENDED"]
ParticipantStateValue = Literal["INVITED", "JOINED", "DECLINED", "LEFT", "MISSED"]
EndReasonValue = Literal["ENDED_BY_USER", "EVERYONE_LEFT", "TARGET_CLOSED"]


class CallParticipantResponse(_Frozen):
    user_id: UUID = Field(description="The account's internal ID; also the participant identity in the voice room")
    full_name: str
    role: Literal["ADMIN", "SUPERVISOR", "GUARD"]
    state: ParticipantStateValue
    invited_at: datetime
    joined_at: datetime | None
    left_at: datetime | None
    is_me: bool


class CallSessionResponse(_Frozen):
    id: UUID
    scope: CallScopeValue
    alert_id: UUID | None
    incident_id: UUID | None
    status: CallStatusValue
    media: Literal["AUDIO"] = "AUDIO"
    started_by: UUID | None
    started_by_name: str | None
    started_by_role: Literal["ADMIN", "SUPERVISOR", "GUARD"]
    started_at: datetime
    ended_at: datetime | None
    end_reason: EndReasonValue | None
    joined_count: int
    my_state: ParticipantStateValue | None = Field(description="The caller's own state; null if never part of it")
    can_end: bool = Field(description="The caller may end the call for everyone (staff or the starter)")
    participants: list[CallParticipantResponse]
    # Decision D-026: what the call is about, for incoming-call banners on the web and the Guard app.
    target_title: str = Field(default="", description='"Weapon alert · Gate Camera" or "INC-000007 · Case title"')
    target_location: str | None = Field(default=None, description="Building → floor → area of the alert or case")

    @classmethod
    def of(cls, call: CallSession, me: UUID, *, is_staff: bool) -> "CallSessionResponse":
        mine = call.participant(me)
        return cls(
            id=call.id,
            scope=call.target.scope.value,  # type: ignore[arg-type]
            alert_id=call.target.alert_id,
            incident_id=call.target.incident_id,
            status=call.status.value,  # type: ignore[arg-type]
            started_by=call.started_by,
            started_by_name=call.started_by_name,
            started_by_role=call.started_by_role,  # type: ignore[arg-type]
            started_at=call.started_at,
            ended_at=call.ended_at,
            end_reason=None if call.end_reason is None else call.end_reason.value,  # type: ignore[arg-type]
            joined_count=call.joined_count,
            my_state=None if mine is None else mine.state.value,  # type: ignore[arg-type]
            can_end=call.status.value == "ACTIVE" and (is_staff or call.started_by == me),
            participants=[
                CallParticipantResponse(
                    user_id=p.user_id,
                    full_name=p.full_name,
                    role=p.role,  # type: ignore[arg-type]
                    state=p.state.value,  # type: ignore[arg-type]
                    invited_at=p.invited_at,
                    joined_at=p.joined_at,
                    left_at=p.left_at,
                    is_me=p.user_id == me,
                )
                for p in call.participants
            ],
            target_title=call.target_title,
            target_location=call.target_location,
        )


class VoiceConnectionResponse(_Frozen):
    provider: Literal["livekit"]
    url: str = Field(description="The LiveKit server, e.g. wss://my-project.livekit.cloud")
    room: str
    identity: str
    token: str = Field(description="Short-lived room token for this participant only; never store it")
    expires_in: int = Field(description="Seconds until the token can no longer be used to connect")

    @classmethod
    def of(cls, grant: VoiceGrant) -> "VoiceConnectionResponse":
        return cls(
            provider="livekit",
            url=grant.url,
            room=grant.room,
            identity=grant.identity,
            token=grant.token,
            expires_in=grant.expires_in_seconds,
        )


class CallJoinResponse(_Frozen):
    outcome: Literal["STARTED", "JOINED"]
    call: CallSessionResponse
    connection: VoiceConnectionResponse


class ActiveCallResponse(_Frozen):
    voice_calls_available: bool = Field(description="False while VOICE_CALL_PROVIDER=none")
    provider: Literal["none", "livekit"]
    call: CallSessionResponse | None


class LeaveCallResponse(_Frozen):
    call: CallSessionResponse
    call_ended: bool = Field(description="True when the call ended because nobody was left in it")


class ResponderResponse(_Frozen):
    """A Guard who acknowledged an alert or is responding to it (Guard app screen 6 item 6). Guard IDs are not
    shown to other Guards."""

    full_name: str
    state: Literal["ACKNOWLEDGED", "RESPONDING"]
    assessment: Literal["UNDER_CONTROL", "ADDITIONAL_GUARDS_REQUIRED"] | None
    reason: Literal["LOCATION", "ESCALATION", "AUTOMATIC", "ASSISTANCE"]
    acknowledged_at: datetime | None
    responding_at: datetime | None
    is_me: bool


class DeviceTokenRequest(_Strict):
    token: str = Field(min_length=16, max_length=4096, description="FCM registration token or APNs device token")
    platform: Literal["ANDROID", "IOS"]
    provider: Literal["FCM", "APNS"]
    app_version: str | None = Field(default=None, max_length=40)


class DeviceTokenResponse(_Frozen):
    id: UUID
    platform: Literal["ANDROID", "IOS"]
    provider: Literal["FCM", "APNS"]
    push_delivery_mode: Literal["none", "fcm"] = Field(
        description="none: stored but nothing is pushed yet; fcm: alerts are pushed to Android (FCM) tokens"
    )


class RealtimeStatusResponse(_Frozen):
    websocket_path: str = "/api/v1/realtime/ws"
    heartbeat_seconds: int
    auth_timeout_seconds: int
    voice_call_provider: Literal["none", "livekit"]
    voice_calls_available: bool
    push_delivery_mode: Literal["none", "fcm"]
