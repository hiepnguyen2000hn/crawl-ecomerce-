"""Reddit qua Apify — tầng dự phòng (T1) khi không có credential API chính thức.

VÌ SAO CẦN TẦNG NÀY

Reddit đã siết việc tạo app: form `/prefs/apps` trả về `success: true` nhưng không
tạo app, chỉ hiện thông báo Responsible Builder Policy. Không có `client_id` thì
luồng OAuth app-only (T0) không chạy được.

Đây đúng là tình huống mà thiết kế xếp tầng sinh ra để xử lý: nguyên nhân thất bại
của T0 (chính sách nhà cung cấp) và T1 (hết credit) **độc lập với nhau**, nên hiếm
khi cả hai cùng chết. Xem docs/DEV-Design-Crawl-Engine.md §2.

CHỌN ACTOR

Đã đo thực tế hai actor:

  trudax/reddit-scraper-lite    ~$0.0065/item · **KHÔNG có upVotes, commentsCount**
  harshmaur/reddit-scraper-pro  ~$0.0005/item · đủ upVotes, commentsCount, engagement

Chọn `pro`: rẻ hơn 13 lần VÀ có chỉ số tương tác — mà tương tác chính là thứ SRS
Bước 1.4 dùng để lọc "bài viết được thảo luận nhiều". Bản `lite` thiếu hẳn trường đó
nên dù có chạy cũng không lọc được.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import settings
from app.crawl import budget
from app.crawl.outcomes import Outcome

#: ⚠️ KHÔNG đổi actor mà chưa đo thực tế. Đã đo:
#:
#:   harshmaur/reddit-scraper-pro   ~$0.0005/item · tôn trọng maxItems · CÓ upVotes   ← đang dùng
#:   trudax/reddit-scraper-lite     ~$0.0065/item · tôn trọng maxItems · THIẾU upVotes
#:   fatihtahta/reddit-scraper-…    ☠️ BỎ QUA maxItems — xin 10 item, trả 2.693,
#:                                     một lần chạy tốn $3.98. Không dùng.
#:
#: Bài học: `maxItems` là *gợi ý* với nhiều actor, không phải ràng buộc. Trần chi phí
#: bên dưới mới là thứ thật sự chặn, vì nó hủy run đang chạy.
ACTOR_ID = "harshmaur~reddit-scraper-pro"
APIFY_BASE = "https://api.apify.com/v2"
POLL_INTERVAL = 5
TIMEOUT_S = 600


class ApifyRedditError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


async def collect(
    keywords: list[str],
    *,
    timeframe: str = "year",
    max_items: int = 200,
    include_comments: bool = True,
    sort: str = "relevance",
    subreddits: list[str] | None = None,
) -> tuple[Outcome, list[dict], list[dict], int, float, str | None]:
    """Trả (outcome, threads, comments, latency_ms, cost_usd, error)."""
    if not settings.apify_token:
        return Outcome.BLOCKED, [], [], 0, 0.0, "Chưa cấu hình APIFY_TOKEN"

    headers = {"Authorization": f"Bearer {settings.apify_token}"}
    payload = {
        "searchTerms": keywords,
        "searchPosts": True,
        "searchComments": include_comments,
        "searchCommunities": False,
        # `relevance` chứ KHÔNG phải `top`: xếp theo điểm trên phạm vi 1 năm kéo
        # toàn bài viral của sub khổng lồ lên đầu, bất kể có đúng ngách hay không.
        "searchSort": sort,
        "searchTime": timeframe,
        "fastMode": False,
        "maxItems": max_items,
    }
    if subreddits:
        # Giới hạn trong cộng đồng đã biết là đúng ngách — cách chặn nhiễu hiệu
        # quả nhất, nhưng đòi phải biết trước tên sub.
        payload["withinCommunity"] = [s.lstrip("r/").strip() for s in subreddits]

    start = time.monotonic()
    try:
        async with httpx.AsyncClient(headers=headers, timeout=60.0) as client:
            blocked = await budget.guard_before_run(client)
            if blocked:
                return Outcome.QUOTA_EXHAUSTED, [], [], _ms(start), 0.0, blocked

            run = await client.post(f"{APIFY_BASE}/acts/{ACTOR_ID}/runs", json=payload)
            if run.status_code not in (200, 201):
                return (
                    Outcome.UPSTREAM_ERROR, [], [], _ms(start), 0.0,
                    f"Không khởi động được actor ({run.status_code}): {run.text[:200]}",
                )

            data = run.json()["data"]
            run_id, dataset_id = data["id"], data["defaultDatasetId"]

            elapsed = 0
            status = data["status"]
            while elapsed < TIMEOUT_S:
                await asyncio.sleep(POLL_INTERVAL)
                elapsed += POLL_INTERVAL
                s = await client.get(f"{APIFY_BASE}/actor-runs/{run_id}")
                if s.status_code != 200:
                    continue
                info = s.json()["data"]
                status = info["status"]

                # Kiểm tra chi phí ở MỖI vòng poll, không chỉ lúc kết thúc: một run
                # có thể đi từ $0 lên $4 trong vài phút. Chạm trần là hủy ngay.
                spent = float(info.get("usageTotalUsd") or 0.0)
                if budget.over_cap(spent):
                    await budget.abort_run(client, run_id)
                    return (
                        Outcome.QUOTA_EXHAUSTED, [], [], _ms(start), spent,
                        f"Đã hủy: chi phí ${spent:.4f} vượt trần "
                        f"${settings.apify_max_cost_per_run_usd:.2f}",
                    )

                if status not in ("RUNNING", "READY"):
                    break
            else:
                return Outcome.UPSTREAM_ERROR, [], [], _ms(start), 0.0, f"Actor quá hạn {TIMEOUT_S}s"

            cost = float(info.get("usageTotalUsd") or 0.0)

            if status != "SUCCEEDED":
                # Hết credit là chuyện riêng, phải nhảy tầng chứ không retry.
                out = Outcome.QUOTA_EXHAUSTED if "LIMIT" in status.upper() else Outcome.UPSTREAM_ERROR
                return out, [], [], _ms(start), cost, f"Actor kết thúc với trạng thái {status}"

            items_resp = await client.get(
                f"{APIFY_BASE}/datasets/{dataset_id}/items", params={"format": "json"}
            )
            items = items_resp.json() if items_resp.status_code == 200 else []

    except httpx.RequestError as exc:
        return Outcome.UPSTREAM_ERROR, [], [], _ms(start), 0.0, f"Lỗi mạng: {exc}"

    threads = [t for t in (_thread(i) for i in items if i.get("dataType") == "post") if t]
    comments = [c for c in (_comment(i) for i in items if i.get("dataType") == "comment") if c]

    if not threads and not comments:
        return Outcome.EMPTY, [], [], _ms(start), cost, None
    return Outcome.OK, threads, comments, _ms(start), cost, None


# ── Chuẩn hoá về đúng hình dạng mà T0 (OAuth) trả ra ─────────────────────────
# Bắt buộc giống nhau, nếu không thì fallback T0→T1 không trong suốt được và tầng
# trên phải biết dữ liệu đến từ đâu.


def _thread(d: dict[str, Any]) -> dict | None:
    external_id = d.get("id") or (f"t3_{d['parsedId']}" if d.get("parsedId") else None)
    if not external_id:
        return None
    return {
        "source": "reddit",
        "external_id": external_id,
        "subreddit": (d.get("communityName") or "").removeprefix("r/"),
        "title": d.get("title") or "",
        "selftext": (d.get("body") or "").strip() or None,
        "url": d.get("postUrl") or d.get("url"),
        "author": d.get("authorName") or d.get("username"),
        # Actor trả số dạng CHUỖI ("29585") — ép kiểu, không tin vào kiểu sẵn có.
        "score": _int(d.get("upVotes") or d.get("score")),
        "num_comments": _int(d.get("commentsCount")),
        "upvote_ratio": _float(d.get("upvoteRatio")),
        "posted_at": _ts(d.get("createdAt")),
        "matched_keywords": [d["searchTerm"]] if d.get("searchTerm") else [],
        "comments_fetched": False,
        "raw": d,
    }


def _comment(d: dict[str, Any]) -> dict | None:
    external_id = d.get("id") or (f"t1_{d['parsedId']}" if d.get("parsedId") else None)
    body = (d.get("body") or "").strip()
    if not external_id or not body or body in ("[deleted]", "[removed]"):
        return None
    thread_id = d.get("postId") or d.get("parentId")
    if not thread_id:
        return None
    return {
        "source": "reddit",
        "external_id": external_id,
        "thread_external_id": thread_id,
        "subreddit": (d.get("communityName") or "").removeprefix("r/"),
        "body": body,
        "author": d.get("authorName") or d.get("username"),
        "score": _int(d.get("upVotes") or d.get("score")),
        "depth": _int(d.get("depth")) or 0,
        "posted_at": _ts(d.get("createdAt")),
    }


def _int(v: Any) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _ts(v: Any) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)
