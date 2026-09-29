from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AdministrativeRegion(Base):
    __tablename__ = "administrative_regions"
    __table_args__ = (
        Index("ix_administrative_regions_level_parent", "level", "parent_code"),
    )

    code: Mapped[str] = mapped_column(String(10), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    level: Mapped[str] = mapped_column(String(20), nullable=False)
    parent_code: Mapped[str | None] = mapped_column(
        String(10), ForeignKey("administrative_regions.code", ondelete="RESTRICT"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
