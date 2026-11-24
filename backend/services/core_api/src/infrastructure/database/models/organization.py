"""Tables for organizations, campuses and campus registration requests.

The schema (including Row-Level Security) is created by hand-written Alembic
migrations; keep these models in sync with them.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.models.base import Base


class OrganizationModel(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(Text)  # unique ignoring capitalization; stored as entered
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # The Organization ID shown to people (6 letters + 11 digits; older IDs: ORG- + 11 digits).
    # Permanent; not the primary key.
    organization_code: Mapped[str] = mapped_column(Text, server_default=text("app_new_organization_code()"))
    official_email: Mapped[str | None] = mapped_column(Text)
    official_phone: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=text("'ACTIVE'"))  # migration 0006


class CampusModel(Base):
    __tablename__ = "campuses"
    __table_args__ = (
        # Lets other tables reference (organization_id, campus_id) together, so a
        # row can never point at a campus that belongs to a different organization.
        UniqueConstraint("organization_id", "id", name="uq_campuses_org_id"),
        UniqueConstraint("organization_id", "name", name="uq_campuses_org_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("organizations.id"))
    name: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CampusRegistrationRequestModel(Base):
    __tablename__ = "campus_registration_requests"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("organizations.id"))
    # NULL once a rejected request's details are erased (decision I; migration 0024).
    proposed_campus_name: Mapped[str | None] = mapped_column(Text)
    applicant_name: Mapped[str | None] = mapped_column(Text)
    applicant_email: Mapped[str | None] = mapped_column(Text)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(Text)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_campus_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("campuses.id"))
    # Migration 0021: given by the applicant ("Already Registered Organization?").
    applicant_username: Mapped[str | None] = mapped_column(Text)
    applicant_phone: Mapped[str | None] = mapped_column(Text)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    details_erased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OrganizationRegistrationModel(Base):
    """A first-Admin registration waiting for its email OTP. Accessed only through the
    database functions of migration 0004; applicant details are erased once it completes
    or expires."""

    __tablename__ = "organization_registrations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    organization_code: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    organization_name: Mapped[str | None] = mapped_column(Text)
    admin_full_name: Mapped[str | None] = mapped_column(Text)
    username: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)
    password_hash: Mapped[str | None] = mapped_column(Text)
    recipient_digest: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    organization_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("organizations.id"))


class EmailOtpChallengeModel(Base):
    """A one-time code, stored only as a keyed hash."""

    __tablename__ = "email_otp_challenges"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    purpose: Mapped[str] = mapped_column(Text)
    registration_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organization_registrations.id", ondelete="CASCADE")
    )
    recipient_digest: Mapped[str] = mapped_column(Text)
    code_hash: Mapped[str] = mapped_column(Text)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    max_attempts: Mapped[int] = mapped_column(Integer)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
