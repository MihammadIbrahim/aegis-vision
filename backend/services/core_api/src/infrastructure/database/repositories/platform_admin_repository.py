from uuid import UUID, uuid4

from sqlalchemy import Uuid, bindparam, func, insert, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.platform_admin import PlatformAdmin
from src.domain.entities.platform_audit_event import PlatformAuditEvent
from src.domain.exceptions.domain_errors import Conflict
from src.infrastructure.database.models import PlatformAdminModel, PlatformAuditLogModel

_Admin = PlatformAdminModel


class SqlPlatformAdminRepository:
    """Runs on the platform database session (PLATFORM_DATABASE_URL), never the tenant one."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_username(self, username: str, *, for_update: bool = False) -> PlatformAdmin | None:
        return await self._one(select(_Admin).where(func.lower(_Admin.username) == username.lower()), for_update)

    async def get_by_id(self, admin_id: UUID, *, for_update: bool = False) -> PlatformAdmin | None:
        return await self._one(select(_Admin).where(_Admin.id == admin_id), for_update)

    async def get_primary(self, *, for_update: bool = False) -> PlatformAdmin | None:
        return await self._one(select(_Admin).where(_Admin.is_primary.is_(True)), for_update)

    async def _one(self, statement, for_update: bool) -> PlatformAdmin | None:  # type: ignore[no-untyped-def]
        if for_update:
            # Serializes concurrent requests for the same account, so failure counts are exact.
            statement = statement.with_for_update()
        row = (await self._session.execute(statement)).scalar_one_or_none()
        return None if row is None else _to_entity(row)

    async def save_login_state(self, admin: PlatformAdmin) -> None:
        await self._session.execute(
            update(_Admin)
            .where(_Admin.id == admin.id)
            .values(
                failed_login_attempts=admin.failed_login_attempts,
                last_failed_login_at=admin.last_failed_login_at,
                locked_until=admin.locked_until,
                last_login_at=admin.last_login_at,
            )
        )

    async def save_credentials(self, admin: PlatformAdmin) -> None:
        """Through platform_set_admin_credentials(): the database itself increases the token
        version (signing out older tokens) and refuses a change based on stale data."""
        statement = text(
            "SELECT platform_set_admin_credentials(:admin_id, :username, :password_hash, :expected_version)"
        ).bindparams(bindparam("admin_id", type_=Uuid))
        params = {
            "admin_id": admin.id,
            "username": admin.username,
            "password_hash": admin.password_hash,
            "expected_version": admin.token_version - 1,  # the entity already counted this change
        }
        try:
            async with self._session.begin_nested():
                new_version = (await self._session.execute(statement, params)).scalar_one()
        except IntegrityError as exc:
            raise Conflict("That username is already taken.") from exc
        except DBAPIError as exc:
            raise Conflict("The account changed at the same time. Please try again.") from exc
        if new_version != admin.token_version:
            raise Conflict("The account changed at the same time. Please try again.")


class SqlPlatformAuditLog:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(self, event: PlatformAuditEvent) -> None:
        await self._session.execute(
            insert(PlatformAuditLogModel).values(
                id=uuid4(),
                occurred_at=event.occurred_at,
                actor_platform_admin_id=event.actor_platform_admin_id,
                action=event.action,
                outcome=event.outcome,
                metadata_=event.metadata,
                ip_address=event.ip_address,
                user_agent=(event.user_agent or None) and event.user_agent[:512],
            )
        )


def _to_entity(row: PlatformAdminModel) -> PlatformAdmin:
    return PlatformAdmin(
        id=row.id,
        username=row.username,
        password_hash=row.password_hash,
        is_active=row.is_active,
        failed_login_attempts=row.failed_login_attempts,
        last_failed_login_at=row.last_failed_login_at,
        locked_until=row.locked_until,
        last_login_at=row.last_login_at,
        created_at=row.created_at,
        is_primary=row.is_primary,
        token_version=row.token_version,
    )
