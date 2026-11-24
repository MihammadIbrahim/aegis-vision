"""Importing this package registers every table on Base.metadata (used by Alembic)."""

from src.infrastructure.database.models.base import Base
from src.infrastructure.database.models.camera import CameraModel
from src.infrastructure.database.models.organization import (
    CampusModel,
    CampusRegistrationRequestModel,
    EmailOtpChallengeModel,
    OrganizationModel,
    OrganizationRegistrationModel,
)
from src.infrastructure.database.models.platform_admin import (
    PlatformAdminModel,
    PlatformAuditLogModel,
)
from src.infrastructure.database.models.user import (
    AdminLoginOtpChallengeModel,
    AdminLoginSessionModel,
    UserCredentialModel,
    UserModel,
)

__all__ = [
    "AdminLoginOtpChallengeModel",
    "AdminLoginSessionModel",
    "Base",
    "CameraModel",
    "CampusModel",
    "CampusRegistrationRequestModel",
    "EmailOtpChallengeModel",
    "OrganizationModel",
    "OrganizationRegistrationModel",
    "PlatformAdminModel",
    "PlatformAuditLogModel",
    "UserCredentialModel",
    "UserModel",
]
