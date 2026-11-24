"""Roles and statuses. The database stores these exact string values."""

from enum import StrEnum


class UserRole(StrEnum):
    ADMIN = "ADMIN"
    SUPERVISOR = "SUPERVISOR"
    GUARD = "GUARD"


class AccountStatus(StrEnum):
    ACTIVE = "ACTIVE"
    LEFT = "LEFT"


class CampusStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class CampusRequestStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
