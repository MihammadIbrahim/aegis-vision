"""Cameras of one campus (migrations 0001, 0013 and 0014).

Queries filter by organization and campus explicitly; Row-Level Security enforces the same rule
again in the database, and lets only Admins and Supervisors write."""

from typing import Any
from uuid import UUID

from sqlalchemy import Uuid, bindparam, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.camera import Camera, ConnectionType
from src.domain.entities.location import LocationTarget
from src.domain.exceptions.domain_errors import Conflict, ValidationFailed
from src.domain.value_objects.location_path import LocationPath

_COLUMNS = """x.id, x.organization_id, x.campus_id, x.display_name, x.connection_type, x.stream_url,
       x.created_at, x.updated_at,
       b.id AS building_id, b.name AS building_name, b.kind AS building_kind,
       f.id AS floor_id, f.name AS floor_name, a.id AS area_id, a.name AS area_name"""
# The location is optional, so every join is a LEFT JOIN.
_JOINS = """LEFT JOIN areas a ON a.id = x.area_id
            LEFT JOIN floors f ON f.id = COALESCE(x.floor_id, a.floor_id)
            LEFT JOIN buildings b ON b.id = COALESCE(x.building_id, f.building_id, a.building_id)"""
_SCOPE = "x.organization_id = :org AND x.campus_id = :campus"
_UUIDS = ("id", "org", "campus", "building_id", "floor_id", "area_id")

_CONFLICTS = {
    "uq_cameras_name": ("CAMERA_NAME_TAKEN", "A camera with this display name already exists in this campus."),
    "uq_cameras_stream": ("CAMERA_STREAM_TAKEN", "Another camera in this campus already uses this stream URL."),
    # Alerts keep their camera (D-012, D-013): a camera with alert history cannot be deleted.
    "fk_alerts_camera": ("CAMERA_HAS_ALERTS", "This camera has alert history, so it cannot be deleted."),
}


def _sql(statement: str, params: dict[str, Any]):  # type: ignore[no-untyped-def]
    return text(statement).bindparams(*(bindparam(n, type_=Uuid) for n in _UUIDS if n in params))


def _constraint(error: IntegrityError) -> str | None:
    cause = getattr(error.orig, "__cause__", None)
    return getattr(cause, "constraint_name", None)


def _to_entity(row: Any) -> Camera:
    location = None
    if row.building_id is not None:
        location = LocationPath(
            row.building_id,
            row.building_name,
            row.building_kind,
            row.floor_id,
            row.floor_name,
            row.area_id,
            row.area_name,
        )
    return Camera(
        id=row.id,
        organization_id=row.organization_id,
        campus_id=row.campus_id,
        display_name=row.display_name,
        connection_type=ConnectionType(row.connection_type),
        stream_url=row.stream_url,
        location=location,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _link(location: LocationTarget | None) -> dict[str, UUID | None]:
    ids: dict[str, UUID | None] = {"building_id": None, "floor_id": None, "area_id": None}
    if location is not None:
        ids[f"{location.level.value.lower()}_id"] = location.id
    return ids


class SqlCameraRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _run(self, statement: str, params: dict[str, Any]) -> Any:
        try:
            return await self._session.execute(_sql(statement, params), params)
        except IntegrityError as error:
            name = _constraint(error)
            if name in _CONFLICTS:
                code, message = _CONFLICTS[name]
                raise Conflict(message, code=code) from error
            if name and name.startswith("ck_cameras_"):
                raise ValidationFailed("The camera details are not valid.", code="INVALID_CAMERA") from error
            raise Conflict("The camera's location is not valid.", code="INVALID_LOCATION") from error

    async def list_cameras(self, organization_id: UUID, campus_id: UUID) -> list[Camera]:
        rows = await self._run(
            f"SELECT {_COLUMNS} FROM cameras x {_JOINS} WHERE {_SCOPE} ORDER BY x.display_name",
            {"org": organization_id, "campus": campus_id},
        )
        return [_to_entity(r) for r in rows]

    async def get_camera(self, camera_id: UUID, organization_id: UUID, campus_id: UUID) -> Camera | None:
        row = (
            await self._run(
                f"SELECT {_COLUMNS} FROM cameras x {_JOINS} WHERE {_SCOPE} AND x.id = :id",
                {"id": camera_id, "org": organization_id, "campus": campus_id},
            )
        ).one_or_none()
        return None if row is None else _to_entity(row)

    async def add_camera(self, camera: Camera, location: LocationTarget | None) -> None:
        await self._run(
            "INSERT INTO cameras (id, organization_id, campus_id, display_name, connection_type, stream_url, "
            "building_id, floor_id, area_id) VALUES (:id, :org, :campus, :name, :type, :url, "
            ":building_id, :floor_id, :area_id)",
            {
                "id": camera.id,
                "org": camera.organization_id,
                "campus": camera.campus_id,
                "name": camera.display_name,
                "type": camera.connection_type.value,
                "url": camera.stream_url,
                **_link(location),
            },
        )

    async def update_camera(self, camera: Camera, location: LocationTarget | None) -> None:
        await self._run(
            "UPDATE cameras x SET display_name = :name, connection_type = :type, stream_url = :url, "
            "building_id = :building_id, floor_id = :floor_id, area_id = :area_id, updated_at = now() "
            f"WHERE {_SCOPE} AND x.id = :id",
            {
                "id": camera.id,
                "org": camera.organization_id,
                "campus": camera.campus_id,
                "name": camera.display_name,
                "type": camera.connection_type.value,
                "url": camera.stream_url,
                **_link(location),
            },
        )

    async def delete_camera(self, camera_id: UUID, organization_id: UUID, campus_id: UUID) -> bool:
        result = await self._run(
            f"DELETE FROM cameras x WHERE {_SCOPE} AND x.id = :id RETURNING x.id",
            {"id": camera_id, "org": organization_id, "campus": campus_id},
        )
        return result.first() is not None
