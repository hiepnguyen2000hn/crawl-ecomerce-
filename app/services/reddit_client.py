"""Connector Reddit — Voice of Customer cho SRS Bước 1.4 (chỉ số S1.4, 25% của S1).

Dùng **OAuth app-only** (`client_credentials`) qua `oauth.reddit.com`, KHÔNG dùng
endpoint `.json` công khai: endpoint đó nay bị siết rất chặt và trả 403 với phần lớn
IP datacenter, nên nó chỉ hoạt động ở máy dev rồi chết ngay khi lên server.

Reddit bắt buộc User-Agent mô tả thật, dạng `platform:app-id:version (by /u/user)`;
UA chung chung bị chặn.

Luồng theo SRS Bước 1.4:
    1. Tìm bài theo bộ từ khoá ngách, giới hạn `timeframe = year`
    2. Xếp hạng subreddit theo số bài khớp → ra "cộng đồng hot"
    3. Cào bình luận của những bài tương tác cao nhất

Trích Pain Points / Deep Desires / Mentioned Competitors là việc của AI Service.
"""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.config import settings
from app.crawl import http
from app.crawl.outcomes import Outcome
from app.services import reddit_apify, voc_filter

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
API_BASE = "https://oauth.reddit.com"

_token: str | None = None
_token_expires_at: float = 0.0


class RedditAuthError(Exception):
    pass


@dataclass
class RedditVocResult:
    outcome: Outcome
    keywords: list[str]
    threads: list[dict] = field(default_factory=list)
    comments: list[dict] = field(default_factory=list)
    top_subreddits: list[dict] = field(default_factory=list)
    latency_ms: int = 0
    error: str | None = None
    filter_report: dict | None = None
    """Báo cáo lọc nhiễu — trả thẳng ra kết quả job chứ không chôn trong log.
    Tỉ lệ loại >80% nghĩa là từ khoá quá rộng; 0% nghĩa là bộ lọc không chạy."""
    tier: str = "oauth"
    """'oauth' (T0, miễn phí) hoặc 'apify' (T1, trả phí). Phải lộ ra ngoài để biết
    một lần thu thập tốn tiền hay không, và để phát hiện khi T0 hỏng lâu ngày."""
    cost_usd: float = 0.0


async def _get_token() -> str:
    """Token app-only, cache lại tới gần hết hạn (Reddit cấp ~24h)."""
    global _token, _token_expires_at

    if _token and time.monotonic() < _token_expires_at:
        return _token

    if not settings.reddit_client_id or not settings.reddit_client_secret:
        raise RedditAuthError(
            "Thiếu REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET. "
            "Tạo app kiểu 'script' tại https://www.reddit.com/prefs/apps"
        )

    resp = await http.fetch(
        TOKEN_URL,
        method="POST",
        data={"grant_type": "client_credentials"},
        auth=(settings.reddit_client_id, settings.reddit_client_secret),
        headers={"User-Agent": settings.reddit_user_agent},
    )
    payload = resp.json() or {}
    token = payload.get("access_token")
    if not token:
        raise RedditAuthError(f"Không lấy được token (HTTP {resp.status_code}): {resp.text[:200]}")

    _token = token
    _token_expires_at = time.monotonic() + int(payload.get("expires_in", 3600)) - 300
    return token


async def _api(path: str, params: dict[str, Any]) -> tuple[Outcome | None, Any, int]:
    token = await _get_token()
    resp = await http.fetch(
        f"{API_BASE}{path}",
        params=params,
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": settings.reddit_user_agent,
        },
    )
    if resp.outcome is not None:
        return resp.outcome, None, resp.latency_ms

    data = resp.json()
    if not isinstance(data, dict) or "data" not in data:
        return Outcome.PARSE_FAIL, None, resp.latency_ms
    return None, data["data"], resp.latency_ms


async def collect_voc(
    keywords: list[str],
    *,
    timeframe: str = "year",
    threads_per_keyword: int = 50,
    comment_threads: int = 10,
    comments_per_thread: int = 60,
    sort: str = "relevance",
    subreddits: list[str] | None = None,
    min_relevance: float = 0.4,
) -> RedditVocResult:
    """Thu thập trọn gói cho một ngách, đi theo chuỗi tầng T0 → T1.

    `timeframe="year"` khớp yêu cầu SRS "trong vòng 1 năm đổ lại".

    T0 (OAuth chính thức) miễn phí nhưng cần `client_id`/`secret` — mà Reddit hiện
    đã siết việc tạo app. Không có credential, hoặc bị chặn, thì rơi xuống T1 (Apify).
    Tầng dưới trả về **đúng một hình dạng dữ liệu** với tầng trên nên chuyển tầng là
    trong suốt với phần còn lại của hệ thống.
    """
    mode = (settings.reddit_provider or "auto").lower()
    have_oauth = bool(settings.reddit_client_id and settings.reddit_client_secret)

    if mode == "apify" or (mode == "auto" and not have_oauth):
        return await _collect_via_apify(
            keywords, timeframe, threads_per_keyword * len(keywords),
            sort=sort, subreddits=subreddits, min_relevance=min_relevance,
        )

    result = await _collect_via_oauth(
        keywords, timeframe, threads_per_keyword, comment_threads, comments_per_thread
    )
    # Chỉ hạ tầng khi T0 hỏng vì phía nhà cung cấp — EMPTY là câu trả lời hợp lệ,
    # tụt xuống tầng trả phí để lấy lại cùng một "không có gì" là đốt tiền vô ích.
    if mode == "auto" and result.outcome in (Outcome.BLOCKED, Outcome.UPSTREAM_ERROR):
        fallback = await _collect_via_apify(
            keywords, timeframe, threads_per_keyword * len(keywords),
            sort=sort, subreddits=subreddits, min_relevance=min_relevance,
        )
        if fallback.outcome is Outcome.OK:
            fallback.error = f"T0 thất bại ({result.error}) → đã dùng T1 Apify"
            return fallback
    return result


async def _collect_via_apify(
    keywords: list[str],
    timeframe: str,
    max_items: int,
    *,
    sort: str = "relevance",
    subreddits: list[str] | None = None,
    min_relevance: float = 0.4,
) -> RedditVocResult:
    outcome, threads, comments, latency, cost, error = await reddit_apify.collect(
        keywords, timeframe=timeframe, max_items=max_items, sort=sort, subreddits=subreddits
    )

    report = None
    if threads:
        threads, _dropped, report = voc_filter.filter_threads(
            threads, keywords, min_score=min_relevance
        )
        kept_ids = {t["external_id"] for t in threads}
        comments = [c for c in comments if c["thread_external_id"] in kept_ids]
        if not threads:
            outcome = Outcome.EMPTY
    result = RedditVocResult(
        outcome=outcome, keywords=keywords, threads=threads, comments=comments,
        latency_ms=latency, error=error, tier="apify", cost_usd=cost,
        filter_report=report,
    )
    if threads:
        counter: Counter[str] = Counter()
        engagement: Counter[str] = Counter()
        for t in threads:
            counter[t["subreddit"]] += 1
            engagement[t["subreddit"]] += t["score"] + t["num_comments"]
        result.top_subreddits = [
            {"subreddit": s, "thread_count": n, "engagement": engagement[s]}
            for s, n in counter.most_common(20)
        ]
    return result


async def _collect_via_oauth(
    keywords: list[str],
    timeframe: str,
    threads_per_keyword: int,
    comment_threads: int,
    comments_per_thread: int,
) -> RedditVocResult:
    result = RedditVocResult(outcome=Outcome.OK, keywords=keywords)
    seen: dict[str, dict] = {}

    try:
        for kw in keywords:
            outcome, data, latency = await _api(
                "/search",
                {
                    "q": kw,
                    "t": timeframe,
                    "sort": "top",
                    "limit": min(threads_per_keyword, 100),
                    "type": "link",
                    "raw_json": 1,
                },
            )
            result.latency_ms += latency

            if outcome is not None:
                result.outcome = outcome
                result.error = f"Tìm kiếm thất bại ở từ khoá '{kw}'"
                break

            for child in data.get("children") or []:
                thread = _normalize_thread(child.get("data") or {}, kw)
                if not thread:
                    continue
                existing = seen.get(thread["external_id"])
                if existing:
                    # Cùng bài khớp nhiều từ khoá → gộp, không nhân bản.
                    existing["matched_keywords"] = sorted(
                        set(existing["matched_keywords"]) | set(thread["matched_keywords"])
                    )
                else:
                    seen[thread["external_id"]] = thread

    except RedditAuthError as exc:
        result.outcome = Outcome.BLOCKED
        result.error = str(exc)
        return result

    threads = sorted(seen.values(), key=lambda t: t["score"], reverse=True)
    result.threads = threads

    if not threads:
        if result.outcome is Outcome.OK:
            result.outcome = Outcome.EMPTY
        return result

    # Cộng đồng hot — xếp hạng subreddit theo số bài khớp và tổng tương tác.
    counter: Counter[str] = Counter()
    engagement: Counter[str] = Counter()
    for t in threads:
        counter[t["subreddit"]] += 1
        engagement[t["subreddit"]] += t["score"] + t["num_comments"]
    result.top_subreddits = [
        {"subreddit": sub, "thread_count": n, "engagement": engagement[sub]}
        for sub, n in counter.most_common(20)
    ]

    # Bình luận mới là nơi chứa "nỗi đau" thật — tiêu đề bài thường chỉ là câu hỏi.
    for t in threads[:comment_threads]:
        outcome, comments, latency = await _fetch_comments(
            t["subreddit"], t["external_id"], comments_per_thread
        )
        result.latency_ms += latency
        if outcome is not None:
            continue  # thiếu bình luận 1 bài không làm hỏng cả lần thu thập
        result.comments.extend(comments)
        t["comments_fetched"] = True

    result.outcome = Outcome.OK
    return result


async def _fetch_comments(
    subreddit: str, thread_fullname: str, limit: int
) -> tuple[Outcome | None, list[dict], int]:
    thread_id = thread_fullname.removeprefix("t3_")
    token = await _get_token()
    resp = await http.fetch(
        f"{API_BASE}/r/{subreddit}/comments/{thread_id}",
        params={"limit": limit, "sort": "top", "depth": 3, "raw_json": 1},
        headers={"Authorization": f"Bearer {token}", "User-Agent": settings.reddit_user_agent},
    )
    if resp.outcome is not None:
        return resp.outcome, [], resp.latency_ms

    data = resp.json()
    # Endpoint này trả về mảng [bài, cây bình luận] — khác hình dạng /search.
    if not isinstance(data, list) or len(data) < 2:
        return Outcome.PARSE_FAIL, [], resp.latency_ms

    out: list[dict] = []
    _walk_comments(data[1].get("data", {}).get("children") or [], thread_fullname, subreddit, 0, out)
    return None, out, resp.latency_ms


def _walk_comments(
    children: list, thread_fullname: str, subreddit: str, depth: int, out: list[dict]
) -> None:
    for child in children:
        if child.get("kind") != "t1":
            continue  # bỏ "more" placeholder
        d = child.get("data") or {}
        body = (d.get("body") or "").strip()
        if not body or body in ("[deleted]", "[removed]"):
            continue

        out.append(
            {
                "source": "reddit",
                "external_id": d.get("name") or f"t1_{d.get('id')}",
                "thread_external_id": thread_fullname,
                "subreddit": subreddit,
                "body": body,
                "author": d.get("author"),
                "score": int(d.get("score") or 0),
                "depth": depth,
                "posted_at": _ts(d.get("created_utc")),
            }
        )

        replies = d.get("replies")
        if isinstance(replies, dict):
            _walk_comments(
                replies.get("data", {}).get("children") or [],
                thread_fullname,
                subreddit,
                depth + 1,
                out,
            )


def _normalize_thread(d: dict, keyword: str) -> dict | None:
    external_id = d.get("name") or (f"t3_{d['id']}" if d.get("id") else None)
    if not external_id:
        return None
    permalink = d.get("permalink") or ""
    return {
        "source": "reddit",
        "external_id": external_id,
        "subreddit": d.get("subreddit") or "",
        "title": d.get("title") or "",
        "selftext": (d.get("selftext") or "").strip() or None,
        "url": f"https://www.reddit.com{permalink}" if permalink else d.get("url"),
        "author": d.get("author"),
        "score": int(d.get("score") or 0),
        "num_comments": int(d.get("num_comments") or 0),
        "upvote_ratio": d.get("upvote_ratio"),
        "posted_at": _ts(d.get("created_utc")),
        "matched_keywords": [keyword],
        "comments_fetched": False,
        "raw": d,
    }


def _ts(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    except (TypeError, ValueError):
        return None
