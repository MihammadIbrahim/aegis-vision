"""Routine-recording retention of the campus (decision D-023)."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.application.dtos.recording_dto import CampusUsageResponse, StorageStatusResponse
from src.application.use_cases.storage_settings_use_cases import StorageOverview
from src.domain.entities.retention_policy import RetentionPolicy, RetentionUnit


class RetentionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    value: int
    unit: RetentionUnit
    seconds: int
    is_default: bool = Field(description="True while the campus has not set its own retention")
    updated_at: datetime | None
    updated_by: UUID | None
    updated_by_name: str | None

    @classmethod
    def of(cls, p: RetentionPolicy) -> "RetentionResponse":
        return cls(
            value=p.value,
            unit=p.unit,
            seconds=p.seconds,
            is_default=p.is_default,
            updated_at=p.updated_at,
            updated_by=p.updated_by,
            updated_by_name=p.updated_by_name,
        )


class StorageOverviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    retention: RetentionResponse
    default_retention: RetentionResponse
    storage: StorageStatusResponse | None = Field(description="None until the retention daemon has reported")
    usage: CampusUsageResponse = Field(description="What this campus stores: routine recordings and evidence")
    provider: str = Field(
        default="LOCAL_DISK", description="Where media is stored (local disk under CCTV_STORAGE_ROOT)"
    )
    warning_percent: int
    critical_percent: int

    @classmethod
    def of(cls, o: StorageOverview) -> "StorageOverviewResponse":
        return cls(
            retention=RetentionResponse.of(o.retention),
            default_retention=RetentionResponse.of(o.default_retention),
            storage=None if o.storage is None else StorageStatusResponse.of(o.storage),
            usage=CampusUsageResponse.of(o.usage),
            warning_percent=o.warning_percent,
            critical_percent=o.critical_percent,
        )


class RetentionChangeRequest(BaseModel):
    """Admins and Supervisors; confirmed with the signed-in user's own password."""

    model_config = ConfigDict(extra="forbid")

    value: int = Field(ge=1, le=100000)
    unit: RetentionUnit
    current_password: str = Field(min_length=1, max_length=256)
