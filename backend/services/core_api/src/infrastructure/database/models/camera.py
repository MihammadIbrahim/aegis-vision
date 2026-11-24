import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKeyConstraint, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.models.base import Base


class CameraModel(Base):
    __tablename__ = "cameras"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "campus_id"],
            ["campuses.organization_id", "campuses.id"],
            name="fk_cameras_campus",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    campus_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    display_name: Mapped[str] = mapped_column(Text)
    connection_type: Mapped[str] = mapped_column(Text, server_default="RTSP")  # migration 0014
    stream_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    building_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)  # migration 0013
    floor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    area_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
