import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Integer, Text, Uuid, func
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.models.base import Base


class PlatformAdminModel(Base):
    """Created by migrations 0002 and 0003. Readable only by the platform database role."""

    __tablename__ = "platform_admins"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    username: Mapped[str] = mapped_column(Text)
    password_hash: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_failed_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    password_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)  # migration 0003
    token_version: Mapped[int] = mapped_column(Integer, default=1)  # migration 0003


class PlatformAuditLogModel(Base):
    """Migration 0003. The platform role may insert and read rows, never change them."""

    __tablename__ = "platform_audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    actor_platform_admin_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    action: Mapped[str] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(Text)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    ip_address: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)
