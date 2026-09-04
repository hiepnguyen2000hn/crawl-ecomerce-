from datetime import date
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class TimePreset(str, Enum):
    PAST_HOUR = "now 1-H"
    PAST_DAY = "now 1-d"
    PAST_7_DAYS = "now 7-d"
    PAST_30_DAYS = "today 1-m"
    PAST_90_DAYS = "today 3-m"
    PAST_12_MONTHS = "today 12-m"
    PAST_5_YEARS = "today 5-y"


class TrendsRequest(BaseModel):
    q: list[str] = Field(..., min_length=1, max_length=5, description="Keywords (max 5)")
    geo: str = Field(default="", description="Country code e.g. VN, US. Empty = worldwide")
    cat: int = Field(default=0, description="Category ID. 0 = all categories")
    gprop: str = Field(default="", description="Google property: web, images, news, youtube, froogle")

    # Time range — either preset or custom
    date_range: TimePreset | None = Field(default=TimePreset.PAST_12_MONTHS)
    start_date: date | None = Field(default=None, description="Custom range start (YYYY-MM-DD)")
    end_date: date | None = Field(default=None, description="Custom range end (YYYY-MM-DD)")

    @model_validator(mode="after")
    def validate_time_range(self) -> "TrendsRequest":
        if self.start_date or self.end_date:
            if not (self.start_date and self.end_date):
                raise ValueError("Both start_date and end_date are required for custom range")
            if self.start_date >= self.end_date:
                raise ValueError("start_date must be before end_date")
            # Custom range overrides preset
            self.date_range = None
        return self

    def build_serpapi_date(self) -> str:
        if self.start_date and self.end_date:
            return f"{self.start_date.strftime('%Y-%m-%d')} {self.end_date.strftime('%Y-%m-%d')}"
        return self.date_range.value if self.date_range else TimePreset.PAST_12_MONTHS.value


class InterestPoint(BaseModel):
    date: str
    values: dict[str, int]  # keyword -> interest value (0-100)


class TrendsResponse(BaseModel):
    request_id: str
    keywords: list[str]
    geo: str
    date_range_used: str
    interest_over_time: list[InterestPoint]
    latency_ms: int


class RelatedQueriesRequest(BaseModel):
    q: str = Field(..., description="Single keyword for related queries")
    geo: str = Field(default="")
    date_range: TimePreset | None = Field(default=TimePreset.PAST_12_MONTHS)
    start_date: date | None = None
    end_date: date | None = None

    @model_validator(mode="after")
    def validate_time_range(self) -> "RelatedQueriesRequest":
        if self.start_date or self.end_date:
            if not (self.start_date and self.end_date):
                raise ValueError("Both start_date and end_date required for custom range")
            if self.start_date >= self.end_date:
                raise ValueError("start_date must be before end_date")
            self.date_range = None
        return self

    def build_serpapi_date(self) -> str:
        if self.start_date and self.end_date:
            return f"{self.start_date.strftime('%Y-%m-%d')} {self.end_date.strftime('%Y-%m-%d')}"
        return self.date_range.value if self.date_range else TimePreset.PAST_12_MONTHS.value


class RelatedQuery(BaseModel):
    query: str
    value: str | int


class RelatedQueriesResponse(BaseModel):
    request_id: str
    keyword: str
    geo: str
    date_range_used: str
    top: list[RelatedQuery]
    rising: list[RelatedQuery]
    latency_ms: int


class AuditLogEntry(BaseModel):
    id: int
    request_id: str
    endpoint: str
    request_params: dict[str, Any] | None
    status: str
    http_status_code: int | None
    latency_ms: int | None
    error_message: str | None
    created_at: str

    class Config:
        from_attributes = True
