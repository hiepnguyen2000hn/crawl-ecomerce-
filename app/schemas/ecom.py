from pydantic import BaseModel, Field, field_validator

from app.crawl.normalize import normalize_domain
from app.sources.amazon.normalize import DEFAULT_MARKETPLACE, MARKETPLACES


class RunScoped(BaseModel):
    """Field chung cho mọi request khởi tạo crawl.

    `run_id` do `levelup_be` phát khi mở một lượt nghiên cứu, AI Service truyền
    nguyên si xuống đây. Nhờ nó `crawl_attempts` gộp được chi phí của nhiều lần
    crawl khác nguồn về đúng một lượt research. Xem docs/DEV-Design-Crawl-Engine.md §6.
    """

    run_id: str | None = Field(
        default=None,
        max_length=64,
        description="Lượt research bên levelup_be. Bỏ trống khi gọi tay để debug.",
    )


class ShopifyScanRequest(RunScoped):
    shop_domain: str = Field(
        ...,
        description="Domain store, vd 'examplestore.com'. Nhận cả URL đầy đủ — sẽ tự cắt.",
        examples=["allbirds.com"],
    )
    max_pages: int = Field(default=10, ge=1, le=100, description="250 sản phẩm/trang")
    currency: str | None = Field(
        default=None,
        min_length=3,
        max_length=3,
        description="Bỏ trống thì tự dò qua /meta.json. Chỉ điền khi store không bật endpoint đó.",
    )

    @field_validator("shop_domain")
    @classmethod
    def _clean_domain(cls, v: str) -> str:
        domain = normalize_domain(v)
        if not domain or "." not in domain:
            raise ValueError("shop_domain không hợp lệ")
        return domain

    @field_validator("currency")
    @classmethod
    def _upper(cls, v: str | None) -> str | None:
        return v.upper() if v else v


class BolSearchRequest(RunScoped):
    query: str = Field(..., min_length=1, description="Từ khoá bản địa (tiếng Hà Lan cho thị trường NL)")
    max_pages: int = Field(default=3, ge=1, le=20, description="Chỉ dùng cho tier browser")
    max_items: int = Field(
        default=50, ge=1, le=500, description="Chỉ dùng cho tier vendor — mỗi item là tiền"
    )
    fetch_details: bool = Field(
        default=False,
        description=(
            "Vào trang chi tiết từng sản phẩm để lấy mô tả · đặc điểm · danh mục. "
            "Trang kết quả tìm kiếm của Bol KHÔNG có ba thứ đó. Đắt hơn ~3,3 lần "
            "($0,01/SP so với $0,003) nên chỉ bật cho sản phẩm đã vào shortlist."
        ),
    )
    country_path: str = Field(
        default="/nl/nl",
        description="'/nl/nl' cho Hà Lan, '/be/nl' cho Bỉ — cùng catalog, khác giá",
    )


class AmazonSearchRequest(RunScoped):
    query: str = Field(..., min_length=1, description="Từ khoá theo ngôn ngữ của marketplace")
    marketplace: str = Field(
        default=DEFAULT_MARKETPLACE,
        description="SRS nhắm DE · FR · IT · ES. NL/BE là sân của Bol.com, không phải Amazon.",
        examples=["amazon.de"],
    )
    max_pages: int = Field(default=2, ge=1, le=10, description="Chỉ dùng cho tier browser")
    max_items: int = Field(default=50, ge=1, le=500, description="Chỉ dùng cho tier vendor")

    @field_validator("marketplace")
    @classmethod
    def _known_marketplace(cls, v: str) -> str:
        domain = normalize_domain(v)
        if domain not in MARKETPLACES:
            raise ValueError(
                f"marketplace '{v}' chưa được hỗ trợ. Hợp lệ: {', '.join(sorted(MARKETPLACES))}"
            )
        return domain


class AlibabaSearchRequest(RunScoped):
    """Dùng chung cho 1688 và Taobao — cùng nhà aggregator, cùng tham số."""

    query: str = Field(
        ...,
        min_length=1,
        description="Từ khoá. Tiếng Trung cho kết quả tốt hơn hẳn tiếng Anh trên cả hai sàn.",
        examples=["便携榨汁机"],
    )
    max_pages: int = Field(default=2, ge=1, le=10, description="Chỉ dùng cho tier browser")
    max_items: int = Field(
        default=50, ge=1, le=500, description="Chỉ dùng cho tier vendor — mỗi item là tiền"
    )
    include_details: bool = Field(
        default=False,
        description="Vào trang chi tiết để lấy bậc giá theo số lượng. Đắt hơn 4 lần.",
    )


class JobAccepted(BaseModel):
    job_id: str
    status: str = "QUEUED"


class ProductListItem(BaseModel):
    id: int
    source: str
    shop_domain: str
    external_id: str
    title: str
    url: str | None
    brand: str | None
    currency: str | None
    price_min_minor: int | None
    price_max_minor: int | None
    rating: float | None
    review_count: int | None
    sales_volume: int | None
    available: bool | None
    seller: str | None
    is_sponsored: bool | None
    image_refs: list | None
    first_seen_at: str | None
    last_seen_at: str | None
