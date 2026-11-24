from pydantic import BaseModel, ConfigDict

from src.domain.entities.user import AdminDirectoryEntry


class AdminDirectoryEntryResponse(BaseModel):
    # Only these two fields may ever leave the API for this endpoint.
    model_config = ConfigDict(extra="forbid", frozen=True)

    full_name: str
    is_first_admin: bool

    @classmethod
    def from_entity(cls, entry: AdminDirectoryEntry) -> "AdminDirectoryEntryResponse":
        return cls(full_name=entry.full_name, is_first_admin=entry.is_first_admin)


class AdminDirectoryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    admins: list[AdminDirectoryEntryResponse]
