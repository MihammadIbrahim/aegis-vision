import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.models.base import Base


class UserModel(Base):
    __tablename__ = "users"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "campus_id"],
            ["campuses.organization_id", "campuses.id"],
            name="fk_users_campus",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    campus_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    role: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    full_name: Mapped[str] = mapped_column(Text)
    email: Mapped[str] = mapped_column(Text)
    display_id: Mapped[str] = mapped_column(Text)
    is_first_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Added in migration 0004. Empty for accounts created before organization registration existed.
    username: Mapped[str | None] = mapped_column(Text)  # unique ignoring capitalization
    phone: Mapped[str | None] = mapped_column(Text)


class UserCredentialModel(Base):
    """Password hashes of organization users, apart from `users` so that no tenant query can
    read them. The app role has no rights on this table."""

    __tablename__ = "user_credentials"

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    password_hash: Mapped[str] = mapped_column(Text)
    password_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Sign-in protection (migration 0006).
    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_failed_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AdminLoginSessionModel(Base):
    """A sign-in that passed the password check and waits for its email code (migration 0006)."""

    __tablename__ = "admin_login_sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AdminLoginOtpChallengeModel(Base):
    """A sign-in code, stored only as a keyed hash."""

    __tablename__ = "admin_login_otp_challenges"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    login_session_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("admin_login_sessions.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id", ondelete="CASCADE"))
    code_hash: Mapped[str] = mapped_column(Text)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    max_attempts: Mapped[int] = mapped_column(Integer)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
