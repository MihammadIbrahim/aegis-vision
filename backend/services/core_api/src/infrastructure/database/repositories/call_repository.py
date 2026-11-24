"""Voice-call records (migration 0030, decision D-025). Every query runs as the signed-in user, so Row-Level Security
limits calls to alerts and cases the user may see (``app_comm_target_visible``)."""

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.call import (
    CallParticipant,
    CallScope,
    CallSession,
    CallStatus,
    CallTarget,
    EndReason,
    ParticipantState,
)
from src.domain.entities.user import User

_SESSION_COLUMNS = """s.id, s.scope, s.alert_id, s.incident_id, s.room_name, s.status, s.started_by,
       su.full_name AS started_by_name, s.started_by_role, s.started_at, s.ended_at, s.end_reason,
       a.alert_type, a.camera_name, i.number AS incident_number, i.title AS incident_title,
       COALESCE(a.building_name, i.building_name) AS building_name,
       COALESCE(a.floor_name, i.floor_name) AS floor_name,
       COALESCE(a.area_name, i.area_name) AS area_name"""
_SESSION_JOINS = """LEFT JOIN users su ON su.id = s.started_by
       LEFT JOIN alerts a ON a.id = s.alert_id
       LEFT JOIN incidents i ON i.id = s.incident_id"""


def _target_title(row: Any) -> str:
    if row.alert_type is not None:
        kind = str(row.alert_type).replace("_", " / " if row.alert_type == "FIGHT_VIOLENCE" else " ").title()
        return f"{kind} alert · {row.camera_name}"
    if row.incident_number is not None:
        return f"INC-{int(row.incident_number):06d} · {row.incident_title}"
    return "Call"


def _target_location(row: Any) -> str | None:
    parts = [p for p in (row.building_name, row.floor_name, row.area_name) if p]
    return " → ".join(parts) if parts else None


class SqlCallRepository:
    def __init__(self, session: AsyncSession, actor: User) -> None:
        self._session = session
        self._actor = actor

    async def _rows(self, sql: str, params: dict[str, Any] | None = None) -> list[Any]:
        return list((await self._session.execute(text(sql), params or {})).all())

    # --- Targets --------------------------------------------------------------------------------------------------

    async def target_status(self, target: CallTarget) -> str | None:
        """The alert's or case's status, or None when the actor may not see it."""
        table = "alerts" if target.scope is CallScope.ALERT else "incidents"
        status: str | None = await self._session.scalar(
            text(
                f"""SELECT t.status FROM {table} t
                     WHERE t.id = :id AND app_comm_target_visible(CAST(:alert AS uuid), CAST(:incident AS uuid))"""
            ),
            {"id": target.target_id, "alert": target.alert_id, "incident": target.incident_id},
        )
        return status

    async def invitees(self, target: CallTarget) -> list[UUID]:
        rows = await self._rows(
            "SELECT app_call_invitees(CAST(:alert AS uuid), CAST(:incident AS uuid)) AS id",
            {"alert": target.alert_id, "incident": target.incident_id},
        )
        return [r.id for r in rows]

    # --- Reading --------------------------------------------------------------------------------------------------

    async def _participants(self, call_ids: list[UUID]) -> dict[UUID, list[CallParticipant]]:
        if not call_ids:
            return {}
        rows = await self._rows(
            """SELECT p.call_id, p.user_id, u.full_name, u.role, p.state, p.invited_at, p.joined_at, p.left_at
                 FROM call_participants p JOIN users u ON u.id = p.user_id
                WHERE p.call_id = ANY(:ids)
                ORDER BY p.joined_at NULLS LAST, u.full_name""",
            {"ids": call_ids},
        )
        found: dict[UUID, list[CallParticipant]] = {i: [] for i in call_ids}
        for r in rows:
            found[r.call_id].append(
                CallParticipant(
                    r.user_id, r.full_name, r.role, ParticipantState(r.state), r.invited_at, r.joined_at, r.left_at
                )
            )
        return found

    async def _sessions(self, where: str, params: dict[str, Any]) -> list[CallSession]:
        rows = await self._rows(
            f"""SELECT {_SESSION_COLUMNS} FROM call_sessions s {_SESSION_JOINS}
                 WHERE {where} ORDER BY s.started_at DESC LIMIT 50""",
            params,
        )
        participants = await self._participants([r.id for r in rows])
        return [
            CallSession(
                id=r.id,
                target=CallTarget(CallScope(r.scope), r.alert_id, r.incident_id),
                room_name=r.room_name,
                status=CallStatus(r.status),
                started_by=r.started_by,
                started_by_name=r.started_by_name,
                started_by_role=r.started_by_role,
                started_at=r.started_at,
                ended_at=r.ended_at,
                end_reason=None if r.end_reason is None else EndReason(r.end_reason),
                participants=participants.get(r.id, []),
                target_title=_target_title(r),
                target_location=_target_location(r),
            )
            for r in rows
        ]

    async def get(self, call_id: UUID) -> CallSession | None:
        found = await self._sessions("s.id = :id", {"id": call_id})
        return found[0] if found else None

    async def active_for(self, target: CallTarget) -> CallSession | None:
        column = "alert_id" if target.scope is CallScope.ALERT else "incident_id"
        found = await self._sessions(f"s.{column} = :id AND s.status = 'ACTIVE'", {"id": target.target_id})
        return found[0] if found else None

    async def ringing_or_joined(self) -> list[CallSession]:
        """Active calls the actor is invited to or has joined."""
        return await self._sessions(
            """s.status = 'ACTIVE' AND EXISTS (SELECT 1 FROM call_participants p WHERE p.call_id = s.id
                   AND p.user_id = :me AND p.state IN ('INVITED', 'JOINED'))""",
            {"me": self._actor.id},
        )

    # --- Writing --------------------------------------------------------------------------------------------------

    async def create(self, call_id: UUID, room: str, target: CallTarget) -> bool:
        """Starts a call with the actor joined. False when another call on the target became active first (the
        unique index); nothing is written then."""
        try:
            async with self._session.begin_nested():
                await self._session.execute(
                    text(
                        """INSERT INTO call_sessions (id, organization_id, campus_id, scope, alert_id, incident_id,
                                                      room_name, started_by, started_by_role)
                           VALUES (:id, :org, :campus, :scope, :alert, :incident, :room, :me, :role)"""
                    ),
                    {
                        "id": call_id,
                        "org": self._actor.organization_id,
                        "campus": self._actor.campus_id,
                        "scope": target.scope.value,
                        "alert": target.alert_id,
                        "incident": target.incident_id,
                        "room": room,
                        "me": self._actor.id,
                        "role": self._actor.role.value,
                    },
                )
        except IntegrityError:
            return False
        await self._session.execute(
            text(
                """INSERT INTO call_participants (call_id, user_id, organization_id, campus_id, state, joined_at)
                   VALUES (:call, :me, :org, :campus, 'JOINED', now())"""
            ),
            {
                "call": call_id,
                "me": self._actor.id,
                "org": self._actor.organization_id,
                "campus": self._actor.campus_id,
            },
        )
        return True

    async def invite(self, call_id: UUID, user_ids: list[UUID]) -> None:
        if not user_ids:
            return
        await self._session.execute(
            text(
                """INSERT INTO call_participants (call_id, user_id, organization_id, campus_id, state)
                   SELECT :call, u, :org, :campus, 'INVITED' FROM unnest(CAST(:users AS uuid[])) AS u
                   ON CONFLICT (call_id, user_id) DO NOTHING"""
            ),
            {"call": call_id, "users": user_ids, "org": self._actor.organization_id, "campus": self._actor.campus_id},
        )

    async def join(self, call_id: UUID) -> None:
        await self._session.execute(
            text(
                """INSERT INTO call_participants (call_id, user_id, organization_id, campus_id, state, joined_at)
                   VALUES (:call, :me, :org, :campus, 'JOINED', now())
                   ON CONFLICT (call_id, user_id) DO UPDATE
                      SET state = 'JOINED', joined_at = COALESCE(call_participants.joined_at, now()), left_at = NULL,
                          updated_at = now()"""
            ),
            {
                "call": call_id,
                "me": self._actor.id,
                "org": self._actor.organization_id,
                "campus": self._actor.campus_id,
            },
        )

    async def set_my_state(self, call_id: UUID, state: ParticipantState) -> None:
        await self._session.execute(
            text(
                """UPDATE call_participants
                      SET state = :state, updated_at = now(),
                          left_at = CASE WHEN :state = 'LEFT' THEN now() ELSE left_at END
                    WHERE call_id = :call AND user_id = :me"""
            ),
            {"call": call_id, "me": self._actor.id, "state": state.value},
        )

    async def end(self, call_id: UUID, reason: EndReason) -> None:
        await self._session.execute(
            text(
                """UPDATE call_participants
                      SET state = CASE state WHEN 'INVITED' THEN 'MISSED' ELSE 'LEFT' END,
                          left_at = CASE WHEN state = 'JOINED' THEN now() ELSE left_at END, updated_at = now()
                    WHERE call_id = :call AND state IN ('INVITED', 'JOINED')"""
            ),
            {"call": call_id},
        )
        await self._session.execute(
            text(
                """UPDATE call_sessions SET status = 'ENDED', ended_at = now(), end_reason = :reason,
                          ended_by = CASE WHEN :reason = 'ENDED_BY_USER' THEN CAST(:me AS uuid) END
                    WHERE id = :call AND status = 'ACTIVE'"""
            ),
            {"call": call_id, "reason": reason.value, "me": self._actor.id},
        )
