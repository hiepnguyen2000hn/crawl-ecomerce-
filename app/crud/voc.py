"""Ghi/đọc dữ liệu Voice of Customer (Reddit)."""

from __future__ import annotations

from typing import Any, Iterable

from sqlalchemy import func as sa_func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from app.models.voc import RedditComment, RedditThread

_THREAD_UPDATABLE = (
    "subreddit",
    "title",
    "selftext",
    "url",
    "author",
    "score",
    "num_comments",
    "upvote_ratio",
    "posted_at",
    "matched_keywords",
    "relevance_score",
    "relevance_reason",
    "comments_fetched",
    "raw",
)


async def upsert_threads(
    db: AsyncSession, threads: Iterable[dict], *, request_id: str | None = None
) -> int:
    rows = [{**t, "request_id": request_id} for t in threads]
    if not rows:
        return 0

    stmt = pg_insert(RedditThread).values(rows)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_reddit_thread_natural",
        set_={
            **{c: stmt.excluded[c] for c in _THREAD_UPDATABLE},
            "last_seen_at": func.now(),
        },
    )
    await db.execute(stmt)
    await db.commit()
    return len(rows)


async def upsert_comments(db: AsyncSession, comments: Iterable[dict]) -> int:
    rows = list(comments)
    if not rows:
        return 0

    # Bình luận là bất biến trên thực tế; chỉ điểm số đổi → không cần cập nhật nội dung.
    stmt = pg_insert(RedditComment).values(rows)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_reddit_comment_natural",
        set_={"score": stmt.excluded.score},
    )
    await db.execute(stmt)
    await db.commit()
    return len(rows)


async def list_threads(
    db: AsyncSession,
    *,
    subreddit: str | None = None,
    min_score: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[RedditThread]:
    q = select(RedditThread).order_by(RedditThread.score.desc())
    if subreddit:
        q = q.where(RedditThread.subreddit == subreddit)
    if min_score is not None:
        q = q.where(RedditThread.score >= min_score)
    result = await db.execute(q.limit(limit).offset(offset))
    return list(result.scalars().all())


async def list_comments(
    db: AsyncSession, *, thread_external_id: str, limit: int = 200
) -> list[RedditComment]:
    result = await db.execute(
        select(RedditComment)
        .where(RedditComment.thread_external_id == thread_external_id)
        .order_by(RedditComment.score.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def top_subreddits(db: AsyncSession, limit: int = 20) -> list[dict]:
    """Cộng đồng hot tính trên toàn bộ dữ liệu đã tích luỹ, không chỉ một lần quét."""
    result = await db.execute(
        select(
            RedditThread.subreddit,
            sa_func.count(RedditThread.id).label("thread_count"),
            sa_func.sum(RedditThread.score + RedditThread.num_comments).label("engagement"),
        )
        .group_by(RedditThread.subreddit)
        .order_by(sa_func.count(RedditThread.id).desc())
        .limit(limit)
    )
    return [
        {"subreddit": r.subreddit, "thread_count": r.thread_count, "engagement": int(r.engagement or 0)}
        for r in result.all()
    ]


def thread_to_dict(t: RedditThread) -> dict[str, Any]:
    return {
        "id": t.id,
        "external_id": t.external_id,
        "subreddit": t.subreddit,
        "title": t.title,
        "selftext": t.selftext,
        "url": t.url,
        "score": t.score,
        "num_comments": t.num_comments,
        "matched_keywords": t.matched_keywords,
        "relevance_score": float(t.relevance_score) if t.relevance_score is not None else None,
        "relevance_reason": t.relevance_reason,
        "comments_fetched": t.comments_fetched,
        "posted_at": t.posted_at.isoformat() if t.posted_at else None,
    }
