"""The campus's routine-recording retention (migration 0023)."""

from uuid import UUID

from sqlalchemy import Uuid, bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.retention_policy import RetentionPolicy, RetentionUnit

_GET = text(
    "SELECT r.retention_value, r.retention_unit, r.updated_at, r.updated_by, u.full_name AS updated_by_name "
    "FROM campus_retention_settings r LEFT JOIN users u ON u.id = r.updated_by "
    "WHERE r.organization_id = :org AND r.campus_id = :campus"
).bindparams(bindparam("org", type_=Uuid), bindparam("campus", type_=Uuid))

_SET = text(
    "INSERT INTO campus_retention_settings "
    "(organization_id, campus_id, retention_value, retention_unit, updated_by, updated_at) "
    "VALUES (:org, :campus, :value, :unit, :actor, now()) "
    "ON CONFLICT (organization_id, campus_id) DO UPDATE "
    "SET retention_value = EXCLUDED.retention_value, retention_unit = EXCLUDED.retention_unit, "
    "updated_by = EXCLUDED.updated_by, updated_at = now()"
).bindparams(bindparam("org", type_=Uuid), bindparam("campus", type_=Uuid), bindparam("actor", type_=Uuid))


class SqlStorageSettingsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_retention(self, organization_id: UUID, campus_id: UUID) -> RetentionPolicy | None:
        row = (await self._session.execute(_GET, {"org": organization_id, "campus": campus_id})).first()
        if row is None:
            return None
        return RetentionPolicy(
            value=row.retention_value,
            unit=RetentionUnit(row.retention_unit),
            updated_at=row.updated_at,
            updated_by=row.updated_by,
            updated_by_name=row.updated_by_name,
        )

    async def set_retention(
        self, organization_id: UUID, campus_id: UUID, value: int, unit: RetentionUnit, actor_id: UUID
    ) -> None:
        await self._session.execute(
            _SET, {"org": organization_id, "campus": campus_id, "value": value, "unit": unit.value, "actor": actor_id}
        )
