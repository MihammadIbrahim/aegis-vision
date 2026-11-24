"""Push delivery of alert notifications to locked phones (decision D-025; PUSH_DELIVERY_MODE=fcm).

Every PUSH_POLL_SECONDS the API claims unsent, unread alert notifications of active users with an FCM token
(``app_push_claim``: each notification is claimed by exactly one process) and pushes them. A push carries no names,
places or text from the alert: only its kind and IDs, so the phone wakes up, rings, and the Guard app reads the
details over the API. Tokens the provider reports as invalid are forgotten. Notifications older than
PUSH_MAX_AGE_SECONDS are never pushed.
"""

import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.application.interfaces.i_push_sender import IPushSender, PushMessage, PushOutcome

logger = logging.getLogger(__name__)

_TEXT = {
    "ALERT_NEW": ("New security alert", "Open the Guard app to acknowledge it."),
    "ALERT_ESCALATED": ("Escalated security alert", "You were added to an alert. Open the Guard app."),
    "ALERT_AUTO_ESCALATED": ("Escalated security alert", "No Guard acknowledged in time. Open the Guard app."),
    "ALERT_ASSISTANCE_BROADCAST": ("Assistance requested", "A Guard needs more Guards. Open the Guard app."),
    "INCIDENT_INVITED": ("Added to a case chat", "Open the Guard app to read the case chat."),
}


def message_for(kind: str, notification_id: str, alert_id: str | None, incident_id: str | None) -> PushMessage:
    title, body = _TEXT.get(kind, ("Security notification", "Open the Guard app."))
    data = {"kind": kind, "notification_id": notification_id}
    if alert_id:
        data["alert_id"] = alert_id
    if incident_id:
        data["incident_id"] = incident_id
    return PushMessage(title=title, body=body, data=data)


async def dispatch_once(
    session_factory: async_sessionmaker[AsyncSession], sender: IPushSender, *, max_age_seconds: int, limit: int = 100
) -> int:
    """Claims and pushes one batch; returns how many pushes were accepted by the provider."""
    async with session_factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT notification_id, kind, alert_id, incident_id, tokens FROM app_push_claim(:limit, :max_age)"
                ),
                {"limit": limit, "max_age": max_age_seconds},
            )
        ).all()
        await session.commit()
    sent = 0
    dead: list[str] = []
    for row in rows:
        message = message_for(
            row.kind,
            str(row.notification_id),
            None if row.alert_id is None else str(row.alert_id),
            None if row.incident_id is None else str(row.incident_id),
        )
        for token in row.tokens:
            outcome = await sender.send(token, message)
            if outcome is PushOutcome.SENT:
                sent += 1
            elif outcome is PushOutcome.INVALID_TOKEN:
                dead.append(token)
    if dead:
        async with session_factory() as session:
            for token in dead:
                await session.execute(text("SELECT app_device_token_forget(:token)"), {"token": token})
            await session.commit()
    return sent


async def run_forever(
    session_factory: async_sessionmaker[AsyncSession], sender: IPushSender, poll_seconds: int, max_age_seconds: int
) -> None:
    while True:
        try:
            await dispatch_once(session_factory, sender, max_age_seconds=max_age_seconds)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Push delivery failed; trying again on the next round")
        await asyncio.sleep(poll_seconds)
