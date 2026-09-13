"""Lọc nhiễu cho dữ liệu Voice of Customer.

VẤN ĐỀ THẬT ĐÃ GẶP

Tìm "kitchen machine" trên Reddit với `sort=top`, `t=year` trả về toàn bài viral của
`r/BeAmazed`, `r/nosleep`, `r/AITAH` — truyện ma và drama, không liên quan máy làm bếp.
Nguyên nhân: sub khổng lồ có upvote áp đảo, nên bất kỳ bài nào khớp lỏng lẻo cũng
leo lên đầu bảng xếp hạng theo điểm.

VÌ SAO PHẢI CHẶN Ở ĐÂY CHỨ KHÔNG CHỈ SỬA CÂU TRUY VẤN

Đưa rác vào prompt "trích nỗi đau khách hàng", LLM **vẫn trả về một danh sách nghe rất
hợp lý** — nó không báo lỗi, không bỏ trống. Rác vào thì ra **rác tự tin**, và không ai
phát hiện vì đầu ra trông hoàn toàn bình thường.

Đây là kiểu hỏng nguy hiểm hơn hẳn lỗi báo đỏ, nên cần một tầng chặn xác định
(deterministic) chứ không phó mặc cho câu truy vấn may rủi.

Tầng lọc này chạy SAU khi đã nhận dữ liệu nên **không tốn thêm tiền** — nó sửa được
cả những thứ mà tham số tìm kiếm không kiểm soát nổi.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Sequence

#: Sub kể chuyện / drama / ảnh vui. Bài ở đây có thể nhắc tới sản phẩm nhưng KHÔNG
#: phải người dùng thật nói về trải nghiệm thật — thứ ta cần cho VOC.
#: `nosleep` là sub truyện kinh dị hư cấu: lấy "nỗi đau" từ đó là bịa hoàn toàn.
DENY_SUBREDDITS = frozenset(
    {
        "nosleep", "beamazed", "aitah", "amitheasshole", "bestofredditorupdates",
        "tifu", "askreddit", "jokes", "funny", "pics", "damnthatsinteresting",
        "interestingasfuck", "mildlyinteresting", "oddlysatisfying", "nextfuckinglevel",
        "relationship_advice", "relationships", "offmychest", "confession",
        "todayilearned", "explainlikeimfive", "showerthoughts", "wholesomememes",
        "memes", "dankmemes", "facepalm", "cursedcomments", "writingprompts",
        # Sub truyện hư cấu — "nỗi đau" ở đây là do tác giả bịa ra cho nhân vật.
        "hfy", "shortstories", "libraryofshadows", "creepypasta", "scp",
        "worldbuilding", "fanfiction", "storiesaboutkevin",
    }
)

#: Từ quá phổ thông, xuất hiện ở mọi nơi nên không mang thông tin ngách.
_STOPWORDS = frozenset(
    {
        "the", "and", "for", "with", "best", "good", "new", "how", "what", "why",
        "van", "een", "het", "die", "der", "und", "mit", "pour", "avec",
        "cho", "cua", "khi", "nao", "gi",
        # Từ mô tả VẤN ĐỀ, không định danh ngách. Người dùng hay đưa vào từ khoá
        # để tìm bài than phiền, nhưng bản thân chúng khớp với mọi thứ trên đời.
        "broke", "broken", "problem", "problems", "issue", "issues", "help",
        "question", "review", "reviews", "worst", "cheap", "need", "want",
    }
)

_TOKEN_RE = re.compile(r"[a-zA-ZÀ-ỹ0-9]{4,}")

#: Khoảng ký tự tối đa giữa các từ khoá khi khớp trong phần thân.
#: ~150 ký tự ≈ một câu — đủ rộng cho một câu mô tả vấn đề, đủ hẹp để loại các từ
#: nằm rải rác trong truyện dài, danh sách việc làm, hay công thức nấu ăn.
#: Thử 250 thì bài về in 3D và máy pha cà phê vẫn lọt qua.
_PROXIMITY_CHARS = 150

#: Độ dài tối thiểu để một token được coi là "từ neo" — đủ đặc trưng để định danh
#: ngách. "processor"(9), "kitchen"(7), "keukenmachine"(13) là từ neo;
#: "food"(4), "broke"(5) thì không.
_ANCHOR_MIN_LEN = 6


def phrase_tokens(keywords: Sequence[str]) -> list[list[str]]:
    """Tách thành **từng cụm riêng**, không gộp chung.

    Gộp chung là sai: truyền ["kitchen machine", "stand mixer dough"] rồi đòi một
    bài khớp tỉ lệ trên cả 5 token thì bài nói đúng về "food processor" cũng bị
    loại, vì nó không có lý do gì phải nhắc tới "dough". Mỗi cụm là một cách diễn
    đạt khác nhau của cùng một ngách — khớp TRỌN một cụm là đủ.
    """
    out: list[list[str]] = []
    for kw in keywords:
        toks = [t for t in _TOKEN_RE.findall(kw.lower()) if t not in _STOPWORDS]
        if toks:
            out.append(toks)
    return out


def tokens_of(keywords: Sequence[str]) -> list[str]:
    """Danh sách token phẳng — chỉ dùng để hiển thị trong báo cáo."""
    seen: list[str] = []
    for group in phrase_tokens(keywords):
        for t in group:
            if t not in seen:
                seen.append(t)
    return seen


def relevance(thread: dict[str, Any], phrases: Sequence[Sequence[str]]) -> tuple[float, str]:
    """Điểm liên quan 0..1 kèm lý do — lấy theo cụm từ khoá khớp nhất.

    Khớp theo **chuỗi con**, không theo từ nguyên vẹn: tiếng Hà Lan và tiếng Đức
    ghép từ ("keukenmachine" nằm trong "keukenmachines", "standmixer" trong
    "standmixerzubehör"), tách theo khoảng trắng là mất hết.
    """
    sub = (thread.get("subreddit") or "").lower()
    if sub in DENY_SUBREDDITS:
        return 0.0, f"sub bi loai: r/{sub}"

    if not phrases:
        return 1.0, "khong co tu khoa de doi chieu"

    title = (thread.get("title") or "").lower()
    body = (thread.get("selftext") or "").lower()

    best = 0.0
    best_reason = "khong tu khoa nao xuat hien trong tieu de/noi dung"

    for toks in phrases:
        # Từ neo phải có mặt thì cụm mới được tính.
        #
        # Không có luật này thì "food processor broke" khớp với "This cat BROKE into
        # my house and ate my FOOD" — 2/3 từ, đủ qua ngưỡng. Nhưng thiếu "processor"
        # thì bài đó chẳng liên quan gì tới sản phẩm.
        #
        # Từ neo = token đủ dài để mang nghĩa định danh ngách. Token ngắn như
        # "food", "broke" xuất hiện khắp nơi nên không đủ để kết luận.
        anchors = [t for t in toks if len(t) >= _ANCHOR_MIN_LEN]
        if anchors:
            haystack = f"{title} {body}"
            if not any(a in haystack for a in anchors):
                continue

        in_title = sum(1 for t in toks if t in title)

        if in_title:
            # Tiêu đề ngắn nên khớp ở đây luôn có nghĩa — không cần xét khoảng cách.
            score = in_title / len(toks)
            if score > best:
                best = min(1.0, score)
                best_reason = f"khop {in_title}/{len(toks)} tu cua cum '{' '.join(toks)}' (tieu de)"
            continue

        in_body = sum(1 for t in toks if t in body)
        if in_body == 0:
            continue

        # Khớp trong phần thân phải KỀ NHAU mới tính.
        #
        # Đây là chỗ bộ lọc suýt thua: r/HFY là sub truyện dài, một chương 10.000 từ
        # chứa đủ "kitchen", "machine", "motor" nằm rải rác ba nơi khác nhau → khớp
        # 3/3 và lọt qua. Tương tự r/waterloo đăng danh sách hàng trăm tin tuyển dụng.
        # Người thật bàn về sản phẩm thì các từ đó đứng gần nhau trong một câu.
        span = _min_span(body, toks)
        if span is None or span > _PROXIMITY_CHARS:
            continue

        score = (in_body / len(toks)) * 0.7
        if score > best:
            best = min(1.0, score)
            best_reason = (
                f"khop {in_body}/{len(toks)} tu cua cum '{' '.join(toks)}' "
                f"(noi dung, cach nhau {span} ky tu)"
            )

    return best, best_reason


def _min_span(text: str, toks: Sequence[str]) -> int | None:
    """Khoảng ký tự ngắn nhất chứa được nhiều token nhất.

    Trả None nếu không có token nào. Dùng để phân biệt "các từ nằm trong cùng một
    câu" với "các từ nằm rải rác khắp một văn bản dài".
    """
    positions: list[int] = []
    for t in toks:
        i = text.find(t)
        while i != -1:
            positions.append(i)
            i = text.find(t, i + 1)
            if len(positions) > 400:  # văn bản rất dài — đủ mẫu để kết luận
                break
    if not positions:
        return None

    present = [t for t in toks if t in text]
    if len(present) == 1:
        return 0  # chỉ một token thì không có khái niệm khoảng cách

    # Trượt cửa sổ: với mỗi vị trí, tìm cửa sổ nhỏ nhất phủ hết các token có mặt.
    positions.sort()
    best = None
    for start in positions:
        window = text[start : start + _PROXIMITY_CHARS]
        covered = sum(1 for t in present if t in window)
        if covered == len(present):
            # Thu hẹp về đúng đoạn chứa token cuối cùng.
            end = max(window.rfind(t) + len(t) for t in present)
            span = end
            if best is None or span < best:
                best = span
    return best if best is not None else _PROXIMITY_CHARS + 1


def filter_threads(
    threads: Iterable[dict],
    keywords: Sequence[str],
    *,
    min_score: float = 0.4,
) -> tuple[list[dict], list[dict], dict[str, Any]]:
    """Trả (giữ lại, loại bỏ, báo cáo).

    Báo cáo trả thẳng vào kết quả job chứ không chôn trong log — tỉ lệ loại bỏ là
    chỉ số chất lượng: loại >80% nghĩa là từ khoá quá rộng, loại 0% nghĩa là bộ lọc
    không hoạt động. Cả hai đều cần người nhìn.
    """
    phrases = phrase_tokens(keywords)
    scored: list[dict] = []

    for t in threads:
        score, reason = relevance(t, phrases)
        t["relevance_score"] = round(score, 3)
        t["relevance_reason"] = reason
        scored.append(t)

    # ── Suy ra cộng đồng đúng ngách, rồi nới tay với bài nằm trong đó ────────
    #
    # Chấm theo chữ một mình quá khắt khe: r/Sourdough là sub đúng ngách nhất mà
    # bị loại 11 giữ 4, vì người ta viết "my starter won't rise in the mixer"
    # chứ không viết đúng cụm từ khoá.
    #
    # Nhưng nếu MỘT SỐ bài trong sub đó khớp mạnh, thì cả sub đó đang bàn đúng
    # chủ đề — và một bài ở đó có ít nhất một dấu hiệu từ khoá thì đáng giữ.
    # Đây chính là "Top Subreddits" mà SRS Bước 1.4 muốn tìm, dùng ngược lại
    # làm bằng chứng ngữ cảnh.
    niche = _niche_subreddits(scored, min_score)

    kept: list[dict] = []
    dropped: list[dict] = []
    for t in scored:
        sub = (t.get("subreddit") or "").lower()
        score = t["relevance_score"]
        if score >= min_score:
            kept.append(t)
        elif score > 0 and sub in niche:
            t["relevance_reason"] += f" + thuoc cong dong dung nganh r/{t['subreddit']}"
            kept.append(t)
        else:
            dropped.append(t)

    kept.sort(key=lambda x: (x["relevance_score"], x.get("score", 0)), reverse=True)

    report = {
        "terms": tokens_of(keywords),
        "min_score": min_score,
        "input": len(kept) + len(dropped),
        "kept": len(kept),
        "dropped": len(dropped),
        "drop_rate": round(len(dropped) / max(1, len(kept) + len(dropped)), 3),
        "dropped_subreddits": _top_subs(dropped),
        "kept_subreddits": _top_subs(kept),
        "niche_subreddits": sorted(niche),
    }
    return kept, dropped, report


def _niche_subreddits(threads: Sequence[dict], min_score: float, min_hits: int = 2) -> set[str]:
    """Sub có từ `min_hits` bài khớp mạnh → coi như đang bàn đúng chủ đề.

    Ngưỡng 2 chứ không phải 1: một bài khớp mạnh có thể là trùng hợp trong sub lớn,
    hai bài trở lên thì khó là ngẫu nhiên.
    """
    from collections import Counter

    strong: Counter[str] = Counter()
    for t in threads:
        if t["relevance_score"] >= min_score:
            sub = (t.get("subreddit") or "").lower()
            if sub and sub not in DENY_SUBREDDITS:
                strong[sub] += 1
    return {s for s, n in strong.items() if n >= min_hits}


def _top_subs(threads: Sequence[dict], limit: int = 8) -> list[str]:
    from collections import Counter

    c = Counter((t.get("subreddit") or "?") for t in threads)
    return [f"r/{s}({n})" for s, n in c.most_common(limit)]
