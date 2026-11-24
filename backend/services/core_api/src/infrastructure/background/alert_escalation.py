"""Automatic escalation of alerts no Guard acknowledged in time (migration 0026, decision D-024), run by the
API every ``ALERT_ESCALATION_POLL_SECONDS``. Each run escalates at most a batch; a failure is logged and the
next run tries again."""

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.infrastructure.database.repositories.alert_repository import SqlAlertOperations

logger = logging.getLogger(__name__)


async def escalate_once(session_factory: async_sessionmaker[AsyncSession]) -> int:
    async with session_factory() as session:
        escalated = await SqlAlertOperations(session).escalate_overdue()
        await session.commit()
    if escalated:
        logger.info("automatically escalated %d alert(s)", escalated)
    return escalated


async def run_forever(session_factory: async_sessionmaker[AsyncSession], interval_seconds: int) -> None:
    while True:
        try:
            await escalate_once(session_factory)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001  (keep running; the next run retries)
            logger.exception("automatic alert escalation failed")
        await asyncio.sleep(interval_seconds)
