"""Voice of Customer — dữ liệu thảo luận Reddit cho SRS Bước 1.4 (chỉ số S1.4).

Phần này KHÔNG có trong tài liệu kỹ thuật nào trước đây: BA bổ sung Reddit VOC ở
SRS v1.1 (09/09/2026) sau khi design doc F2 đã viết xong. S1.4 chiếm 25% của S1.

Crawler chỉ thu thập và lưu thô. Việc trích Pain Points / Deep Desires /
Mentioned Competitors là của AI Service — ngoài phạm vi F2.
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base


class RedditThread(Base):
    """Một bài đăng. Upsert theo (source, external_id) — cùng bài gặp ở nhiều
    từ khoá chỉ cập nhật điểm số, không nhân bản."""

    __tablename__ = "reddit_threads"
    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_reddit_thread_natural"),
        Index("ix_reddit_threads_sub", "subreddit", "posted_at"),
        Index("ix_reddit_threads_score", "score"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    request_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True, index=True)

    source: Mapped[str] = mapped_column(String(32), default="reddit")
    external_id: Mapped[str] = mapped_column(String(64))  # vd "t3_1abc2de"

    subreddit: Mapped[str] = mapped_column(String(128), index=True)
    title: Mapped[str] = mapped_column(Text)
    selftext: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    author: Mapped[str | None] = mapped_column(String(128), nullable=True)

    score: Mapped[int] = mapped_column(Integer, default=0)
    num_comments: Mapped[int] = mapped_column(Integer, default=0)
    upvote_ratio: Mapped[float | None] = mapped_column(nullable=True)

    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)

    matched_keywords: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    """Từ khoá ngách nào đã tìm ra bài này — để AI biết ngữ cảnh khi phân tích."""

    comments_fetched: Mapped[bool] = mapped_column(default=False)
    raw: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RedditComment(Base):
    """Bình luận trong một bài. Đây mới là nơi chứa 'nỗi đau' thật —
    tiêu đề bài thường chỉ là câu hỏi."""

    __tablename__ = "reddit_comments"
    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_reddit_comment_natural"),
        Index("ix_reddit_comments_thread", "thread_external_id", "score"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    source: Mapped[str] = mapped_column(String(32), default="reddit")
    external_id: Mapped[str] = mapped_column(String(64))
    thread_external_id: Mapped[str] = mapped_column(String(64), index=True)

    subreddit: Mapped[str | None] = mapped_column(String(128), nullable=True)
    body: Mapped[str] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(String(128), nullable=True)
    score: Mapped[int] = mapped_column(Integer, default=0)
    depth: Mapped[int] = mapped_column(Integer, default=0)

    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
