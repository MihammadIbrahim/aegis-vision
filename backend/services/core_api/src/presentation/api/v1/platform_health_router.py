"""Platform system health for the Super Admin (decision D-019).

GET /platform/health   Core API, database, media storage (measured) and the planned services
                       (reported NOT_DEPLOYED). Each call measures again (the page's Refresh).
"""

from fastapi import APIRouter, Request

from src.application.dtos.platform_management_dto import HealthResponse
from src.application.use_cases.platform_health_use_cases import (
    CheckPlatformHealth,
    HealthReport,
)
from src.infrastructure.database.repositories.platform_management_repository import (
    SqlPlatformManagementRepository,
)
from src.presentation.dependencies import PlatformAdminDep, PlatformSessionDep

router = APIRouter(prefix="/platform", tags=["Platform Super Admin: health"])


def _pool_status(request: Request) -> dict[str, object]:
    engine = getattr(request.app.state, "platform_engine", None)
    pool = getattr(engine, "pool", None)
    if pool is None:
        return {}
    facts: dict[str, object] = {"description": pool.status()}
    for name in ("size", "checkedout", "overflow", "checkedin"):
        method = getattr(pool, name, None)
        if callable(method):
            facts[name] = method()
    return facts


async def check_health(request: Request, session) -> HealthReport:  # type: ignore[no-untyped-def]
    settings = request.app.state.settings
    repository = SqlPlatformManagementRepository(session)
    return await CheckPlatformHealth(
        ping_database=repository.ping,
        database_facts=repository.database_facts,
        pool_status=lambda: _pool_status(request),
        storage_root=settings.cctv_storage_root,
        storage_warning_percent=settings.storage_warning_percent,
        storage_critical_percent=settings.storage_critical_percent,
        started_at=request.app.state.started_at,
        detection_intake_enabled=settings.detection_ingest_token is not None,
    ).execute()


@router.get("/health", response_model=HealthResponse)
async def health(request: Request, admin: PlatformAdminDep, session: PlatformSessionDep) -> HealthResponse:
    return HealthResponse.of(await check_health(request, session))
