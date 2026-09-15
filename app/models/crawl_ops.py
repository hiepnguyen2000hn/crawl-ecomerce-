"""Hạ tầng vận hành của crawl engine (G1) — không phải dữ liệu nghiệp vụ.

`CrawlSourcePolicy` giữ mọi tham số chống chặn theo nguồn TRONG DB thay vì code,
`CrawlAttempt` ghi lại từng lần thử để đo tỉ lệ thành công. Xem
docs/DEV-Design-Crawl-Engine.md §5, §6.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base


class CrawlSourcePolicy(Base):
    """Tham số chống chặn cho một nguồn. Sửa bằng UPDATE, không cần deploy lại.

    Không có row cho một nguồn → engine dùng default an toàn (`app/crawl/policy.py`),
    không phải lỗi.
    """

    __tablename__ = "crawl_source_policies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32), unique=True, index=True)

    tier_chain: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    """vd `["official", "vendor_rainforest", "browser"]` — thứ tự fallback.

    Ở G1, registry (`app/sources/registry.py`) vẫn quyết định chuỗi adapter thật;
    cột này để tham khảo/ghi chú vận hành, engine chưa lọc theo nó cho tới G2+."""

    max_concurrency: Mapped[int] = mapped_column(Integer, default=2)
    min_delay_ms: Mapped[int] = mapped_column(Integer, default=1500)
    jitter_ms: Mapped[int] = mapped_column(Integer, default=500)
    max_attempts: Mapped[int] = mapped_column(Integer, default=2)

    identity_max_requests: Mapped[int | None] = mapped_column(Integer, nullable=True)
    identity_max_age_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    soft_block_cooldown_s: Mapped[int] = mapped_column(Integer, default=1800)
    respect_retry_after: Mapped[bool] = mapped_column(Boolean, default=True)

    daily_budget_usd: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    """None = không giới hạn. Có giá trị thì engine cộng dồn `crawl_attempts.cost_usd`
    trong ngày UTC hiện tại và chặn trước khi vào tier đầu tiên nếu đã vượt."""

    cache_ttl_seconds: Mapped[int] = mapped_column(Integer, default=0)
    """0 = không cache. Xem docs §9.3 — giá đối thủ nên ngắn, listing nguồn hàng dài hơn."""

    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CrawlAttempt(Base):
    """Một lần thử gọi một adapter. Nhiều dòng có thể chung `request_id` (đổi tier/retry).

    Đây là bảng duy nhất trả lời được "tỉ lệ crawl thành công đang là bao nhiêu %" —
    trước G1 không có gì đo được ngoài đọc log bằng mắt.
    """

    __tablename__ = "crawl_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(UUID(as_uuid=False), index=True)

    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    """Lượt research bên `levelup_be` — 1 run có thể gồm nhiều `request_id` khác nhau
    (quét nhiều store, nhiều nguồn). None nếu request không thuộc lượt research nào
    (vd gọi tay qua Swagger để debug). Xem docs/DEV-Design-Crawl-Engine.md §6, §9.4."""

    source: Mapped[str] = mapped_column(String(32), index=True)
    capability: Mapped[str] = mapped_column(String(32))
    tier: Mapped[str] = mapped_column(String(64))
    """vd 'official' | 'vendor:rainforest' | 'browser' | tên adapter fake khi demo."""

    identity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    """None với adapter kiểu API/không cần danh tính riêng. Đổ dữ liệu thật từ G2."""

    attempt_no: Mapped[int] = mapped_column(Integer)
    outcome: Mapped[str] = mapped_column(String(32), index=True)

    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Numeric(10, 6), default=0)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
