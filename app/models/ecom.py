"""Thực thể TMĐT đã chuẩn hoá — 1 dòng = 1 sản phẩm, KHÔNG phải 1 dòng = 1 lần search.

Khác với `facebook_ads_results` (nhét cả mảng ads vào một ô JSONB), bảng này cho phép
lọc theo `rating >= 4.0`, gộp cụm SKU, và gắn `candidate_id` — những thao tác mà
AI Matching + Scoring bắt buộc phải làm. Xem docs/DEV-Design-Crawl-Engine.md §8.2.
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
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base


class EcomProduct(Base):
    """Một sản phẩm trên một sàn. Upsert theo (source, shop_domain, external_id).

    Gặp lại ở lần quét sau chỉ cập nhật `last_seen_at` + giá — không chèn dòng mới.
    Nhờ đó bảng không phình tuyến tính theo số lần quét.
    """

    __tablename__ = "ecom_products"
    __table_args__ = (
        UniqueConstraint("source", "shop_domain", "external_id", name="uq_ecom_product_natural"),
        Index("ix_ecom_products_seen", "source", "last_seen_at"),
        Index("ix_ecom_products_rating", "source", "rating"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # ── Khoá tự nhiên ────────────────────────────────────────────────────────
    source: Mapped[str] = mapped_column(String(32), index=True)  # 'shopify' | 'bol'
    shop_domain: Mapped[str] = mapped_column(String(255), index=True)
    external_id: Mapped[str] = mapped_column(String(128))

    # ── Nội dung ─────────────────────────────────────────────────────────────
    title: Mapped[str] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    brand: Mapped[str | None] = mapped_column(String(255), nullable=True)
    product_type: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # ── Giá: integer minor unit + mã tiền tệ. KHÔNG dùng float. ──────────────
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    price_min_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    price_max_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # ── Tín hiệu cho S2.2 ────────────────────────────────────────────────────
    rating: Mapped[float | None] = mapped_column(Numeric(3, 2), nullable=True)
    review_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sales_volume: Mapped[int | None] = mapped_column(Integer, nullable=True)
    """Shopify và Bol.com KHÔNG công bố số đã bán → luôn None ở 2 nguồn này.

    SRS chấm S2.2 dựa trên trường này; cần chốt với BA một chỉ số thay thế
    (thứ hạng bestseller / số review), nếu không sản phẩm 2 nguồn này luôn bị 0 điểm.
    Xem docs/DEV-Design-Crawl-Engine.md §2.2.
    """
    bestseller_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    available: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    seller: Mapped[str | None] = mapped_column(String(255), nullable=True)
    """Ai đang bán mẫu này — khác `brand`. Trên marketplace (Bol.com) một sản phẩm có
    nhiều người bán; đây chính là "đối thủ" mà SRS Bước 3 cần nhận diện để so giá."""

    is_sponsored: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    """Vị trí quảng cáo trả tiền, không phải thứ hạng bán chạy tự nhiên.

    Không tách ra thì tín hiệu "top kết quả" bị nhiễu: sàn đẩy quảng cáo lên đầu,
    và ta sẽ chấm điểm một sản phẩm chỉ vì người bán chịu chi tiền ads."""

    image_refs: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    """URL ảnh gốc. Job mirror sẽ thay bằng key trên object storage — ảnh nguồn
    hết hạn hoặc chặn hotlink, không dùng trực tiếp để tính embedding được."""

    raw: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    """Payload gốc để dựng lại khi sửa parser. Không đọc trong luồng nghiệp vụ."""

    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class EcomPricePoint(Base):
    """Lịch sử giá — chỉ ghi khi giá ĐỔI so với lần quan sát gần nhất.

    Ghi mỗi lần quét sẽ làm bảng phình tuyến tính mà không thêm thông tin; việc
    "hôm nay vẫn còn bán" đã nằm ở `EcomProduct.last_seen_at`.

    Bảng append-only → khi chạm ~50GB thì phân vùng theo tháng (§5 design doc).
    """

    __tablename__ = "ecom_price_points"
    __table_args__ = (
        Index("ix_price_points_variant", "source", "shop_domain", "external_variant_id", "observed_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    request_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True, index=True)

    source: Mapped[str] = mapped_column(String(32))
    shop_domain: Mapped[str] = mapped_column(String(255))
    external_product_id: Mapped[str] = mapped_column(String(128), index=True)
    external_variant_id: Mapped[str] = mapped_column(String(128))

    variant_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    sku: Mapped[str | None] = mapped_column(String(128), nullable=True)

    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    price_minor: Mapped[int] = mapped_column(BigInteger)
    compare_at_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    available: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
