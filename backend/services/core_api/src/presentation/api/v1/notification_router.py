"""The signed-in user's in-app notifications (decision D-013). Stored and listed only: nothing is
pushed to phones or browsers yet.

GET    /notifications               newest first (?unread_only=true)
POST   /notifications/{id}/read
POST   /notifications/read-all
"""

from uuid import UUID

from fastapi import APIRouter, Query

from src.application.dtos.notification_dto import NotificationResponse, ReadAllResponse
from src.application.use_cases.notification_use_cases import ManageNotifications
from src.infrastructure.database.repositories.notification_repository import (
    SqlNotificationRepository,
)
from src.infrastructure.database.unit_of_work import SqlTransaction
from src.presentation.dependencies import MemberDep, SessionDep

router = APIRouter(prefix="/notifications", tags=["Notifications"])


def _use_case(session) -> ManageNotifications:  # type: ignore[no-untyped-def]
    return ManageNotifications(SqlNotificationRepository(session), SqlTransaction(session))


@router.get("", response_model=list[NotificationResponse])
async def list_notifications(
    actor: MemberDep, session: SessionDep, unread_only: bool = False, limit: int = Query(default=50, ge=1, le=200)
) -> list[NotificationResponse]:
    return [NotificationResponse.of(n) for n in await _use_case(session).list(unread_only=unread_only, limit=limit)]


@router.post("/{notification_id}/read", status_code=204)
async def read(notification_id: UUID, actor: MemberDep, session: SessionDep) -> None:
    await _use_case(session).read(notification_id)


@router.post("/read-all", response_model=ReadAllResponse)
async def read_all(actor: MemberDep, session: SessionDep) -> ReadAllResponse:
    return ReadAllResponse(marked_read=await _use_case(session).read_all())
