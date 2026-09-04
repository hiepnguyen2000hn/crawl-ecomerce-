from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base


class GoogleTrendsResult(Base):
    __tablename__ = "google_trends_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Link to audit log for observability (request_id is UUID string)
    request_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("api_audit_logs.request_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Query context
    keywords: Mapped[list] = mapped_column(JSONB)          # ["iphone", "samsung"]
    geo: Mapped[str] = mapped_column(String(10), default="")
    date_range: Mapped[str] = mapped_column(String(50))    # "today 12-m" or "2024-01-01 2024-12-31"
    data_type: Mapped[str] = mapped_column(String(30))     # "TIMESERIES" | "RELATED_QUERIES"
    # Source tells us which path was used
    source: Mapped[str] = mapped_column(String(20))        # "serpapi" | "direct"
    # Actual results
    timeline_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    related_queries: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class FacebookAdsResult(Base):
    __tablename__ = "facebook_ads_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("api_audit_logs.request_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Query context
    query: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    page_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    country: Mapped[str] = mapped_column(String(10), default="")
    category: Mapped[str] = mapped_column(String(100), default="all")
    media_type: Mapped[str] = mapped_column(String(50), default="all")
    active_status: Mapped[str] = mapped_column(String(20), default="active")
    min_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    max_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    fetch_details: Mapped[bool] = mapped_column(default=False)
    # Results
    total_ads_count: Mapped[int] = mapped_column(Integer, default=0)
    ads_data: Mapped[list] = mapped_column(JSONB)           # full ads array from Apify

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
