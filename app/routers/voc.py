"""Endpoint Voice of Customer — Reddit (SRS Bước 1.4, chỉ số S1.4).

Crawler chỉ thu thập và lưu. Trích Pain Points / Deep Desires / Mentioned Competitors
là việc của AI Service — nó đọc qua `GET /threads` + `GET /threads/{id}/comments`.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import voc as voc_crud
from app.database import get_db
from app.deps import get_arq, get_job_store
from app.jobs.store import JobStore
from app.schemas.ecom import JobAccepted
from app.schemas.voc import RedditVocRequest

router = APIRouter(prefix="/api/v1/voc", tags=["Voice of Customer"])


@router.post(
    "/reddit/collect",
    status_code=202,
    response_model=JobAccepted,
    summary="Thu thập thảo luận Reddit cho một ngách",
)
async def collect_reddit(
    body: RedditVocRequest,
    store: Annotated[JobStore, Depends(get_job_store)],
    arq=Depends(get_arq),
) -> JobAccepted:
    job_id = str(uuid.uuid4())
    await store.create(job_id, "reddit_voc")
    await arq.enqueue_job(
        "run_reddit_voc",
        job_id,
        body.keywords,
        body.timeframe,
        body.threads_per_keyword,
        body.comment_threads,
        body.comments_per_thread,
        body.sort,
        body.subreddits,
        body.min_relevance,
        body.run_id,
    )
    return JobAccepted(job_id=job_id)


@router.get("/jobs/{job_id}", summary="Xem trạng thái/kết quả job thu thập VOC")
async def get_job(job_id: str, store: Annotated[JobStore, Depends(get_job_store)]) -> dict:
    data = await store.get(job_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy job '{job_id}'")
    return data


@router.get("/threads", summary="Danh sách bài thảo luận đã thu thập")
async def list_threads(
    db: Annotated[AsyncSession, Depends(get_db)],
    subreddit: str | None = Query(default=None),
    min_score: int | None = Query(default=None, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[dict]:
    rows = await voc_crud.list_threads(
        db, subreddit=subreddit, min_score=min_score, limit=limit, offset=offset
    )
    return [voc_crud.thread_to_dict(r) for r in rows]


@router.get(
    "/threads/{thread_external_id}/comments",
    summary="Bình luận của một bài — nơi chứa 'nỗi đau' thật",
)
async def list_comments(
    thread_external_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(default=200, ge=1, le=500),
) -> list[dict]:
    rows = await voc_crud.list_comments(db, thread_external_id=thread_external_id, limit=limit)
    return [
        {
            "external_id": r.external_id,
            "thread_external_id": r.thread_external_id,
            "subreddit": r.subreddit,
            "body": r.body,
            "author": r.author,
            "score": r.score,
            "depth": r.depth,
            "posted_at": r.posted_at.isoformat() if r.posted_at else None,
        }
        for r in rows
    ]


@router.get(
    "/subreddits/top",
    summary="Cộng đồng thảo luận nhiều nhất — SRS Bước 1.4 'Top Subreddits'",
)
async def top_subreddits(
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(default=20, ge=1, le=100),
) -> list[dict]:
    return await voc_crud.top_subreddits(db, limit=limit)
