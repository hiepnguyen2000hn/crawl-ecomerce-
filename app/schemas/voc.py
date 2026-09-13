from pydantic import BaseModel, Field


class RedditVocRequest(BaseModel):
    keywords: list[str] = Field(
        ...,
        min_length=1,
        max_length=10,
        description="Bộ từ khoá ngách bản địa, thường lấy từ Bước 1.1 (AI sinh keyword)",
        examples=[["oversized dress", "oversized dress quality"]],
    )
    timeframe: str = Field(
        default="year",
        description="SRS Bước 1.4 yêu cầu 'trong vòng 1 năm đổ lại' → giữ mặc định 'year'",
    )
    threads_per_keyword: int = Field(default=50, ge=1, le=100)
    comment_threads: int = Field(
        default=10, ge=0, le=50, description="Số bài tương tác cao nhất sẽ cào bình luận"
    )
    comments_per_thread: int = Field(default=60, ge=1, le=200)


class RedditVocJobResult(BaseModel):
    request_id: str
    threads_saved: int
    comments_saved: int
    top_subreddits: list[dict]
    latency_ms: int
