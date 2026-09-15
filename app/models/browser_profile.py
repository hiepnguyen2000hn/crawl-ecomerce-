from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base


class BrowserProfile(Base):
    """
    Mỗi row = 1 account / identity.
    fingerprint_seed → CloakBrowser dùng để tạo fingerprint nhất quán (same seed = same canvas, WebGL, fonts...).
    proxy_url → gán cố định cho profile này (khác với proxy pool chung của SerpAPI).
    """
    __tablename__ = "browser_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    label: Mapped[str] = mapped_column(String(200))

    # Fingerprint seed — integer, same seed = same fingerprint mỗi lần launch
    fingerprint_seed: Mapped[int] = mapped_column(Integer, unique=True)

    # Proxy riêng cho profile (format: http://user:pass@host:port)
    proxy_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Thư mục lưu cookies/localStorage (relative to /profiles volume)
    profile_dir: Mapped[str | None] = mapped_column(String(500), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Trạng thái session hiện tại
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
