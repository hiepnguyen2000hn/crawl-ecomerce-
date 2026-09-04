from typing import Any
from pydantic import BaseModel, Field, model_validator


class FacebookAdsRequest(BaseModel):
    query: str | None = Field(default=None, description="Search keyword, e.g. 'iphone'")
    page_id: str | None = Field(default=None, description="Facebook Page ID")
    max_items: int = Field(default=50, ge=1, le=1000)
    country: str = Field(default="", description="Country code e.g. VN, US. Empty = all")
    category: str = Field(default="all", description="all | POLITICAL_AND_ISSUE_ADS | HOUSING | EMPLOYMENT | FINANCIAL_PRODUCTS")
    media_type: str = Field(default="all", description="all | image | meme | video | none")
    sort_by: str = Field(default="mostRecent", description="mostRecent | impressions")
    active_status: str = Field(default="active", description="active | inactive | all")
    min_date: str | None = Field(default=None, description="YYYY-MM-DD")
    max_date: str | None = Field(default=None, description="YYYY-MM-DD")
    advertisers: list[str] = Field(default_factory=list, description="List of page IDs to filter")
    fetch_details: bool = Field(default=False, description="Fetch extra transparency info per ad")

    @model_validator(mode="after")
    def require_query_or_page_id(self) -> "FacebookAdsRequest":
        if not self.query and not self.page_id:
            raise ValueError("Either 'query' or 'page_id' is required")
        return self

    def to_apify_input(self) -> dict:
        payload: dict = {"maxItems": self.max_items, "fetchDetails": self.fetch_details}
        if self.query:
            payload["query"] = self.query
        if self.page_id:
            payload["pageId"] = self.page_id
        if self.country:
            payload["country"] = self.country
        if self.category != "all":
            payload["category"] = self.category
        if self.media_type != "all":
            payload["mediaType"] = self.media_type
        if self.sort_by:
            payload["sortBy"] = self.sort_by
        if self.active_status:
            payload["activeStatus"] = self.active_status
        if self.min_date:
            payload["minDate"] = self.min_date
        if self.max_date:
            payload["maxDate"] = self.max_date
        if self.advertisers:
            payload["advertisers"] = self.advertisers
        return payload


class FacebookAdsResponse(BaseModel):
    request_id: str
    query: str | None
    page_id: str | None
    country: str
    total_ads: int
    ads: list[dict[str, Any]]
    latency_ms: int
    source: str = "apify"


class FacebookAdsListItem(BaseModel):
    id: int
    request_id: str
    query: str | None
    page_id: str | None
    country: str
    total_ads_count: int
    created_at: str

    class Config:
        from_attributes = True
