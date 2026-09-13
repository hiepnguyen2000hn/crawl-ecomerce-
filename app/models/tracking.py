"""Theo dõi chỉ số sản phẩm theo thời gian — nền cho việc suy ra "bán chạy".

VÌ SAO CẦN BẢNG NÀY

Shopify và Bol.com đều **không công bố số lượng đã bán**. Nhưng "bán chạy" vẫn suy ra
được, bằng **tốc độ thay đổi** của những thứ họ có công bố:

    Sản phẩm A: +40 review / 30 ngày  →  bán gấp ~20 lần
    Sản phẩm B: +2  review / 30 ngày

Tỉ lệ người mua để lại review khá ổn định trong cùng ngành hàng, nên tốc độ tăng review
là proxy tốt cho doanh số. Tương tự, chu kỳ `còn hàng → hết → còn` là bằng chứng hàng
đang chạy.

ĐIỀU QUAN TRỌNG NHẤT: **dữ liệu này không back-fill được.**

Một sản phẩm hôm nay có 194 review — con số đó tự nó vô nghĩa: có thể tích luỹ trong
3 năm, có thể trong 3 tuần. Chỉ khi chụp lại nhiều lần mới phân biệt được. Mỗi ngày
không chụp là một ngày dữ liệu tốc độ mất vĩnh viễn.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base


class ProductMetricSnapshot(Base):
    """Một lần quan sát chỉ số của một sản phẩm.

    Chỉ ghi khi **có chỉ số đổi** so với lần quan sát gần nhất — ghi mỗi lần quét sẽ
    làm bảng phình tuyến tính mà không thêm thông tin nào.

    Dùng khoá tự nhiên chứ không FK sang `ecom_products.id`: job crawl đã có sẵn
    `external_id`, khỏi phải truy ngược lấy id, và cùng quy ước với `ecom_price_points`.

    Bảng append-only → phân vùng theo tháng khi chạm ~50GB (design doc §5).
    """

    __tablename__ = "product_metric_snapshots"
    __table_args__ = (
        Index("ix_metric_snap_product", "source", "shop_domain", "external_product_id", "observed_at"),
        Index("ix_metric_snap_observed", "observed_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    source: Mapped[str] = mapped_column(String(32))
    shop_domain: Mapped[str] = mapped_column(String(255))
    external_product_id: Mapped[str] = mapped_column(String(128))

    review_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rating: Mapped[float | None] = mapped_column(Numeric(3, 2), nullable=True)
    available: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    bestseller_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)

    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class CrawlWatchlist(Base):
    """Thứ cần quét lại định kỳ.

    Không có bảng này thì việc theo dõi phụ thuộc vào người nhớ bấm nút — mà tốc độ
    chỉ có giá trị khi chuỗi quan sát đều đặn và không đứt quãng.
    """

    __tablename__ = "crawl_watchlist"
    __table_args__ = (
        UniqueConstraint("kind", "target", "country_path", name="uq_watchlist_target"),
        Index("ix_watchlist_due", "enabled", "last_run_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    kind: Mapped[str] = mapped_column(String(32))
    """'shopify_store' — target là domain · 'bol_query' — target là từ khoá."""

    target: Mapped[str] = mapped_column(Text)
    country_path: Mapped[str] = mapped_column(String(16), default="")
    """Chỉ dùng cho bol_query: '/nl/nl' (Hà Lan) hoặc '/be/nl' (Bỉ)."""

    max_pages: Mapped[int] = mapped_column(Integer, default=2)
    interval_hours: Mapped[int] = mapped_column(Integer, default=24)
    """24h là mặc định hợp lý: review không tăng đủ nhanh để quét dày hơn có ý nghĩa,
    mà quét dày thì tốn quota và tăng rủi ro bị chặn."""

    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_job_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    run_count: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
