"""Tín hiệu quảng cáo đã chuẩn hoá — SRS Bước 2.1 (chỉ số S2.1, 12.5% tổng điểm).

VÌ SAO ĐÂY LÀ TÍN HIỆU MẠNH NHẤT

`active_days` là **tiền của chính đối thủ** nói lên sự thật: không ai đốt ngân sách
quảng cáo 60 ngày liên tục cho một sản phẩm lỗ. So với "số lượng đã bán" (mà Shopify
lẫn Bol.com đều không công bố), đây là bằng chứng trực tiếp hơn về việc sản phẩm có
đang sinh lời hay không.

Đó cũng là lý do SRS đặt `S2.1` trọng số 50% trong `S2`.

VÌ SAO PHẢI TÁCH BẢNG RIÊNG

`facebook_ads_results` hiện lưu **cả mảng ads vào một ô JSONB** theo từng lần search.
Cách đó ghi nhanh nhưng:
  - không lọc được `active_days > 14` — đúng tiêu chí đầu vào SRS Bước 2.1a yêu cầu
  - cùng một ad gặp lại ở 10 lần search thành 10 bản sao nằm trong 10 blob
  - AI SKU Matching cần mỗi ad là một dòng có id ổn định để gắn `candidate_id`

Bảng này giữ nguyên vai trò đó: một dòng một quảng cáo, upsert theo khoá tự nhiên.
Blob thô vẫn nằm ở `facebook_ads_results` + `api_audit_logs` để dựng lại khi sửa parser.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base


class AdSignal(Base):
    """Một quảng cáo trong thư viện quảng cáo. Upsert theo (source, external_ad_id)."""

    __tablename__ = "ad_signals"
    __table_args__ = (
        UniqueConstraint("source", "external_ad_id", name="uq_ad_signal_natural"),
        # Index này phục vụ đúng truy vấn lọc của SRS Bước 2.1a:
        # "chỉ lấy ads có active_days dài VÀ/HOẶC tương tác vượt trội".
        Index("ix_ad_signals_active", "source", "country", "active_days"),
        Index("ix_ad_signals_page", "page_id"),
        Index("ix_ad_signals_seen", "last_seen_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    request_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True, index=True)

    # ── Khoá tự nhiên ────────────────────────────────────────────────────────
    source: Mapped[str] = mapped_column(String(32), default="meta_ads_library", index=True)
    external_ad_id: Mapped[str] = mapped_column(String(64))

    # ── Nhà quảng cáo ────────────────────────────────────────────────────────
    page_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    page_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    country: Mapped[str | None] = mapped_column(String(8), nullable=True)

    # ── Vòng đời chiến dịch — phần quan trọng nhất ───────────────────────────
    start_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    active_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    """Số ngày quảng cáo đã chạy. Tính khi ghi chứ không tính lúc đọc, để index
    dùng được — lọc `active_days > 14` là truy vấn chạy thường xuyên nhất.

    Ads còn đang chạy thì mốc kết thúc là HÔM NAY, nên con số này tăng dần qua mỗi
    lần quét; đó là hành vi đúng, không phải sai số.
    """

    # ── Tín hiệu quy mô ──────────────────────────────────────────────────────
    reach: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    impressions: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    collation_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    """Số biến thể của cùng một mẫu quảng cáo. Chạy nhiều biến thể = đang tối ưu
    nghiêm túc, thêm một dấu hiệu cho thấy sản phẩm đáng để đầu tư."""

    # ── Nội dung — đầu vào cho AI phân tích & cho đội content ────────────────
    ad_copy: Mapped[str | None] = mapped_column(Text, nullable=True)
    cta_text: Mapped[str | None] = mapped_column(String(255), nullable=True)
    media_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    media_urls: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    landing_page_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    """Mắt xích nối sang SRS Bước 3: giá cần lấy là giá trên landing page của
    ĐỐI THỦ ĐANG CHẠY ADS, không phải giá của một store bất kỳ."""

    ad_library_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)

    # ── Bối cảnh thu thập ────────────────────────────────────────────────────
    matched_query: Mapped[str | None] = mapped_column(Text, nullable=True)
    candidate_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    """Để AI SKU Matching gắn ad này vào một cụm sản phẩm. Crawler không tự điền."""

    raw: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
