from pydantic import BaseModel, Field, field_validator

from app.crawl.normalize import normalize_domain


class ShopifyScanRequest(BaseModel):
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


class BolSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, description="Từ khoá bản địa (tiếng Hà Lan cho thị trường NL)")
    max_pages: int = Field(default=3, ge=1, le=20)
    country_path: str = Field(
        default="/nl/nl",
        description="'/nl/nl' cho Hà Lan, '/be/nl' cho Bỉ — cùng catalog, khác giá",
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
