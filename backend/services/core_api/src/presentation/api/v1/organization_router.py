from fastapi import APIRouter

from src.application.dtos.organization_dto import (
    AdminDirectoryEntryResponse,
    AdminDirectoryResponse,
)
from src.application.use_cases.organization_use_cases import GetAdminDirectory
from src.infrastructure.database.repositories.user_repository import SqlUserRepository
from src.presentation.dependencies import ActorDep, SessionDep

router = APIRouter(prefix="/organization", tags=["Organization"])


@router.get("/admin-directory", response_model=AdminDirectoryResponse)
async def get_admin_directory(actor: ActorDep, session: SessionDep) -> AdminDirectoryResponse:
    """Names of all Admins in the caller's organization, with the First Admin marked.

    There is no organization parameter: the organization is always the caller's own.
    """
    entries = await GetAdminDirectory(SqlUserRepository(session)).execute(actor)
    return AdminDirectoryResponse(admins=[AdminDirectoryEntryResponse.from_entity(entry) for entry in entries])
