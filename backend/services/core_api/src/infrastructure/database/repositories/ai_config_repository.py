"""AI Configuration storage (migrations 0027 and 0033). Staff of a campus read it; campus Admins change the campus
modules and the Supervisor visibility, and staff change cameras (Row-Level Security). The organization's blocks
(set by the Super Admin) are read through ``app_organization_ai_blocks()``. Vision workers read the effective
configuration and report what they run through two functions, with the service token."""

from typing import Any
from uuid import UUID

from sqlalchemy import Uuid, bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.domain.entities.ai_configuration import (
    FEATURE_NAMES,
    NONE,
    AiModule,
    CameraAiConfig,
    CameraAiState,
    CampusAiSettings,
    ModuleSet,
)
from src.domain.entities.user import User

_NAMES = [m.value for m in AiModule]
_COLS = ", ".join(_NAMES)
_VISIBLE = {m.value: f"supervisor_can_manage_{FEATURE_NAMES[m]}" for m in AiModule}
_VISIBLE_COLS = ", ".join(f"{column} AS visible_{name}" for name, column in _VISIBLE.items())


def _modules(row: Any, prefix: str = "") -> ModuleSet:
    return ModuleSet(**{name: bool(getattr(row, f"{prefix}{name}")) for name in _NAMES})


def _uuid_sql(statement: str, *names: str):  # type: ignore[no-untyped-def]
    return text(statement).bindparams(*(bindparam(n, type_=Uuid) for n in names))


_PLATFORM = text(f"SELECT {_COLS} FROM app_platform_ai_modules()")
_BLOCKS = text(f"SELECT {_COLS} FROM app_organization_ai_blocks()")
_CAMPUS = _uuid_sql(
    f"""SELECT {_COLS}, {_VISIBLE_COLS}, updated_at, updated_by_name FROM campus_ai_settings
         WHERE organization_id = :org AND campus_id = :campus""",
    "org",
    "campus",
)
_CAMPUS_SAVE = _uuid_sql(
    f"""INSERT INTO campus_ai_settings AS s (organization_id, campus_id, {_COLS}, {", ".join(_VISIBLE.values())},
                                             updated_by, updated_by_name)
        VALUES (:org, :campus, {", ".join(":" + n for n in _NAMES)}, {", ".join(":visible_" + n for n in _NAMES)},
                :actor, :actor_name)
        ON CONFLICT (organization_id, campus_id) DO UPDATE SET
            {", ".join(f"{n} = EXCLUDED.{n}" for n in [*_NAMES, *_VISIBLE.values()])},
            updated_by = EXCLUDED.updated_by, updated_by_name = EXCLUDED.updated_by_name, updated_at = now()""",
    "org",
    "campus",
    "actor",
)
_CAMERAS = _uuid_sql(
    f"""SELECT c.id, c.display_name, b.name AS building_name, f.name AS floor_name, a.name AS area_name,
               {", ".join(f"COALESCE(s.{n}, false) AS {n}" for n in _NAMES)}, s.camera_id IS NOT NULL AS customized,
               s.updated_by_name, s.updated_at, COALESCE(st.state, 'UNKNOWN') AS stream_state,
               ai.modules AS running, ai.frames_per_second, ai.last_error, ai.reported_at
          FROM cameras c
          LEFT JOIN areas a ON a.id = c.area_id
          LEFT JOIN floors f ON f.id = COALESCE(c.floor_id, a.floor_id)
          LEFT JOIN buildings b ON b.id = COALESCE(c.building_id, f.building_id, a.building_id)
          LEFT JOIN camera_ai_settings s ON s.camera_id = c.id
          LEFT JOIN camera_status st ON st.camera_id = c.id
          LEFT JOIN camera_ai_status ai ON ai.camera_id = c.id
         WHERE c.organization_id = :org AND c.campus_id = :campus
         ORDER BY c.display_name, c.id""",
    "org",
    "campus",
)
_CAMERA_SAVE = _uuid_sql(
    f"""INSERT INTO camera_ai_settings AS s (camera_id, organization_id, campus_id, {_COLS}, updated_by,
                                             updated_by_name)
        VALUES (:camera, :org, :campus, {", ".join(":" + n for n in _NAMES)}, :actor, :actor_name)
        ON CONFLICT (camera_id) DO UPDATE SET
            {", ".join(f"{n} = EXCLUDED.{n}" for n in _NAMES)},
            updated_by = EXCLUDED.updated_by, updated_by_name = EXCLUDED.updated_by_name, updated_at = now()""",
    "camera",
    "org",
    "campus",
    "actor",
)
_SERVICE_CONFIG = _uuid_sql(
    f"""SELECT camera_id, organization_id, campus_id, {_COLS}, confidence_threshold, frame_sample_fps,
               detection_cooldown_seconds FROM app_camera_ai_config(:camera)""",
    "camera",
)
_STATUS_REPORT = _uuid_sql(
    "SELECT app_camera_ai_status_report(:camera, CAST(:modules AS text[]), :fps, :error, :worker)", "camera"
)


class SqlAiConfigRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def platform_modules(self) -> ModuleSet:
        row = (await self._session.execute(_PLATFORM)).first()
        return ModuleSet(False, False, False, False, False) if row is None else _modules(row)

    async def organization_blocks(self) -> ModuleSet:
        row = (await self._session.execute(_BLOCKS)).first()
        return NONE if row is None else _modules(row)

    async def campus_settings(self, actor: User) -> CampusAiSettings | None:
        row = (
            await self._session.execute(_CAMPUS, {"org": actor.organization_id, "campus": actor.campus_id})
        ).first()
        if row is None:
            return None
        return CampusAiSettings(_modules(row), row.updated_at, row.updated_by_name, _modules(row, "visible_"))

    async def save_campus(self, actor: User, modules: ModuleSet, supervisor_visible: ModuleSet) -> None:
        params = {
            "org": actor.organization_id,
            "campus": actor.campus_id,
            "actor": actor.id,
            "actor_name": actor.full_name,
            **{n: getattr(modules, n) for n in _NAMES},
            **{f"visible_{n}": getattr(supervisor_visible, n) for n in _NAMES},
        }
        await self._session.execute(_CAMPUS_SAVE, params)

    async def cameras(self, actor: User) -> list[CameraAiState]:
        rows = await self._session.execute(_CAMERAS, {"org": actor.organization_id, "campus": actor.campus_id})
        result: list[CameraAiState] = []
        for r in rows:
            location = " › ".join(p for p in (r.building_name, r.floor_name, r.area_name) if p) or None
            result.append(
                CameraAiState(
                    camera_id=r.id,
                    camera_name=r.display_name,
                    location=location,
                    configured=_modules(r),
                    customized=bool(r.customized),
                    stream_state=r.stream_state,
                    running=list(r.running or []),
                    frames_per_second=float(r.frames_per_second) if r.frames_per_second is not None else None,
                    last_error=r.last_error,
                    reported_at=r.reported_at,
                    updated_by_name=r.updated_by_name,
                    updated_at=r.updated_at,
                )
            )
        return result

    async def save_camera(self, actor: User, camera_id: UUID, modules: ModuleSet) -> None:
        params = {
            "camera": camera_id,
            "org": actor.organization_id,
            "campus": actor.campus_id,
            "actor": actor.id,
            "actor_name": actor.full_name,
            **{n: getattr(modules, n) for n in _NAMES},
        }
        await self._session.execute(_CAMERA_SAVE, params)

    # --- Vision workers (service token) ------------------------------------------------------------------

    async def service_config(self, camera_id: UUID) -> CameraAiConfig | None:
        row = (await self._session.execute(_SERVICE_CONFIG, {"camera": camera_id})).first()
        if row is None or row.camera_id is None:
            return None
        return CameraAiConfig(
            camera_id=row.camera_id,
            organization_id=row.organization_id,
            campus_id=row.campus_id,
            modules=_modules(row),
            confidence_threshold=float(row.confidence_threshold or 0.5),
            frame_sample_fps=int(row.frame_sample_fps or 5),
            detection_cooldown_seconds=int(row.detection_cooldown_seconds or 60),
        )

    async def report_status(
        self, camera_id: UUID, modules: list[str], fps: float | None, error: str | None, worker: str | None
    ) -> str:
        params = {"camera": camera_id, "modules": modules, "fps": fps, "error": error, "worker": worker}
        return str((await self._session.execute(_STATUS_REPORT, params)).scalar_one())
