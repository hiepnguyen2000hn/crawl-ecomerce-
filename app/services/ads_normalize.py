"""Chuẩn hoá payload Meta Ad Library → `ad_signals`.

VÌ SAO VIẾT KIỂU "THỬ NHIỀU TÊN"

Thư viện quảng cáo Meta không có schema công khai ổn định, và mỗi actor Apify lại
đặt tên trường một kiểu (`ad_archive_id` / `adArchiveID` / `id`). Viết cứng theo một
bộ tên đoán mò thì lần chạy đầu ra toàn `None` mà không biết vì sao.

Nên mỗi trường khai một **danh sách tên khả dĩ**, và hàm `coverage()` đếm xem trường
nào thật sự đọc được. Lần chạy thật đầu tiên tự nói cho ta biết cần sửa gì — thay vì
phải mò từng bước.

`active_days` là lý do bảng này tồn tại: nó là bằng chứng mạnh nhất về việc sản phẩm
có đang sinh lời không, vì đó là tiền của chính đối thủ. Xem docstring app/models/ads.py.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timezone
from typing import Any, Iterable, Sequence

#: Tên trường khả dĩ cho từng thuộc tính, thử theo thứ tự.
ALIASES: dict[str, Sequence[str]] = {
    "external_ad_id": ("ad_archive_id", "adArchiveID", "adArchiveId", "ad_id", "adId", "id"),
    "page_id": ("page_id", "pageID", "pageId", "advertiser_id"),
    "page_name": ("page_name", "pageName", "advertiser_name", "advertiserName"),
    "country": ("country", "countries", "reached_countries", "publisher_country"),
    "start_date": ("start_date", "startDate", "ad_delivery_start_time", "start_date_string"),
    "end_date": ("end_date", "endDate", "ad_delivery_stop_time", "end_date_string"),
    "is_active": ("is_active", "isActive", "active_status", "activeStatus"),
    "reach": ("reach", "reachEstimate", "eu_total_reach", "audience_size"),
    "impressions": ("impressions", "impressionsWithIndex", "impressions_index"),
    "collation_count": ("collation_count", "collationCount"),
    "currency": ("currency",),
    "cta_text": ("cta_text", "ctaText", "call_to_action", "cta_type"),
    "media_type": ("media_type", "mediaType", "display_format", "displayFormat"),
    "ad_library_url": ("ad_library_url", "adLibraryUrl", "url", "permalink"),
}

#: Body quảng cáo hay nằm lồng trong `snapshot`. Thử cả phẳng lẫn lồng.
BODY_PATHS: Sequence[Sequence[str]] = (
    ("ad_creative_body",),
    ("body",),
    ("snapshot", "body", "text"),
    ("snapshot", "body"),
    ("snapshot", "caption"),
    ("snapshot", "title"),
)

LINK_PATHS: Sequence[Sequence[str]] = (
    ("link_url",),
    ("landing_page_url",),
    ("snapshot", "link_url"),
    ("snapshot", "caption"),
    ("ad_creative_link_caption",),
)


def normalize_many(
    items: Iterable[dict],
    *,
    request_id: str | None = None,
    matched_query: str | None = None,
    default_country: str | None = None,
) -> tuple[list[dict], dict[str, int]]:
    """Trả (danh sách đã chuẩn hoá, báo cáo độ phủ từng trường).

    Báo cáo độ phủ để lần chạy đầu tiên tự chỉ ra trường nào chưa map được —
    đưa thẳng vào kết quả job chứ không chôn trong log.
    """
    out: list[dict] = []
    found: Counter[str] = Counter()
    total = 0

    for raw in items:
        total += 1
        row = _normalize_one(
            raw,
            request_id=request_id,
            matched_query=matched_query,
            default_country=default_country,
        )
        if row is None:
            continue
        for k, v in row.items():
            if v not in (None, "", [], {}):
                found[k] += 1
        out.append(row)

    coverage = {"_items": total, "_normalized": len(out), **dict(found)}
    return out, coverage


def _normalize_one(
    raw: dict[str, Any],
    *,
    request_id: str | None,
    matched_query: str | None,
    default_country: str | None = None,
) -> dict | None:
    external_ad_id = _str(_pick(raw, ALIASES["external_ad_id"]))
    if not external_ad_id:
        return None

    start = _as_date(_pick(raw, ALIASES["start_date"]))
    end = _as_date(_pick(raw, ALIASES["end_date"]))
    is_active = _as_bool(_pick(raw, ALIASES["is_active"]))

    return {
        "request_id": request_id,
        "source": "meta_ads_library",
        "external_ad_id": external_ad_id,
        "page_id": _str(_pick(raw, ALIASES["page_id"])),
        "page_name": _str(_pick(raw, ALIASES["page_name"])),
        # Actor không trả country theo từng ad — lấy từ tham số truy vấn.
        # Ads thương mại trên Meta Ad Library vốn không công bố quốc gia phân phối.
        "country": _country(_pick(raw, ALIASES["country"])) or (default_country or None),
        "start_date": start,
        "end_date": end,
        "is_active": is_active,
        "active_days": active_days(start, end, is_active),
        "reach": _int(_pick(raw, ALIASES["reach"])),
        "impressions": _int(_pick(raw, ALIASES["impressions"])),
        "collation_count": _int(_pick(raw, ALIASES["collation_count"])),
        "ad_copy": _deep(raw, BODY_PATHS),
        "cta_text": _str(_pick(raw, ALIASES["cta_text"]))[:255] or None,
        "media_type": _str(_pick(raw, ALIASES["media_type"]))[:32] or None,
        "media_urls": _media(raw) or None,
        "landing_page_url": _deep(raw, LINK_PATHS),
        "ad_library_url": _str(_pick(raw, ALIASES["ad_library_url"])) or _library_url(external_ad_id),
        "currency": _str(_pick(raw, ALIASES["currency"]))[:8] or None,
        "matched_query": matched_query,
        "raw": raw,
    }


def active_days(
    start: date | None, end: date | None, is_active: bool | None = None
) -> int | None:
    """Số ngày quảng cáo đã chạy.

    Ads còn chạy thì chưa có `end_date` — lấy mốc là HÔM NAY. Nghĩa là con số tăng
    dần qua mỗi lần quét, và đó đúng là điều ta muốn đo: một mẫu ads càng sống lâu
    càng chứng tỏ nó đang có lãi.
    """
    if start is None:
        return None
    stop = end if end is not None else date.today()
    if is_active and end is not None and end < date.today():
        stop = date.today()  # cờ active mâu thuẫn end_date quá khứ → tin cờ active
    days = (stop - start).days
    return max(0, days)


# ── Helper đọc dữ liệu chịu được nhiều hình dạng ─────────────────────────────


def _pick(d: dict, keys: Sequence[str]) -> Any:
    """Tìm ở tầng gốc, rồi tìm tiếp trong `snapshot`.

    Meta gói phần sáng tạo (copy, CTA, định dạng, media) vào `snapshot`, còn phần
    hành chính (id, ngày, trang) để ở gốc. Chỉ tìm một tầng là mất hẳn CTA và
    media_type — hai thứ đội content cần.
    """
    snap = d.get("snapshot") if isinstance(d.get("snapshot"), dict) else {}
    for container in (d, snap):
        for k in keys:
            if k in container and container[k] not in (None, ""):
                return container[k]
    return None


def _deep(d: dict, paths: Sequence[Sequence[str]]) -> str | None:
    for path in paths:
        cur: Any = d
        for seg in path:
            if not isinstance(cur, dict) or seg not in cur:
                cur = None
                break
            cur = cur[seg]
        if isinstance(cur, str) and cur.strip():
            return cur.strip()
        if isinstance(cur, dict) and isinstance(cur.get("text"), str):
            return cur["text"].strip()
    return None


def _media(d: dict) -> list[str]:
    """Gom URL ảnh/video từ nhiều chỗ có thể chứa chúng."""
    urls: list[str] = []
    snap = d.get("snapshot") if isinstance(d.get("snapshot"), dict) else {}
    for container in (d, snap):
        for key in ("images", "videos", "media", "image_urls", "video_urls", "cards"):
            val = container.get(key) if isinstance(container, dict) else None
            if isinstance(val, list):
                for item in val:
                    if isinstance(item, str):
                        urls.append(item)
                    elif isinstance(item, dict):
                        for k in (
                            "original_image_url", "resized_image_url", "video_sd_url",
                            "video_hd_url", "video_preview_image_url", "url", "src",
                        ):
                            if isinstance(item.get(k), str):
                                urls.append(item[k])
                                break
    seen: set[str] = set()
    return [u for u in urls if u.startswith("http") and not (u in seen or seen.add(u))]


def _library_url(ad_id: str) -> str:
    return f"https://www.facebook.com/ads/library/?id={ad_id}"


def _country(v: Any) -> str | None:
    if isinstance(v, str):
        return v[:8] or None
    if isinstance(v, (list, tuple)) and v:
        return str(v[0])[:8]
    return None


def _as_date(v: Any) -> date | None:
    """Mốc thời gian có thể là unix giây, unix mili, hoặc chuỗi ISO."""
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)) or (isinstance(v, str) and v.isdigit()):
        ts = float(v)
        if ts > 1e11:  # mili giây
            ts /= 1000
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc).date()
        except (ValueError, OSError, OverflowError):
            return None
    if isinstance(v, str):
        text = v.strip().replace("Z", "+00:00")
        for parse in (datetime.fromisoformat, lambda s: datetime.strptime(s, "%Y-%m-%d")):
            try:
                return parse(text).date()
            except (ValueError, TypeError):
                continue
    return None


def _as_bool(v: Any) -> bool | None:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        low = v.strip().lower()
        if low in ("active", "true", "yes", "1"):
            return True
        if low in ("inactive", "false", "no", "0"):
            return False
    return None


def _int(v: Any) -> int | None:
    if isinstance(v, dict):  # có actor bọc số trong {"lower_bound": ...}
        v = v.get("lower_bound") or v.get("value") or v.get("count")
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _str(v: Any) -> str:
    return str(v).strip() if v not in (None, "") else ""
