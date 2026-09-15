"""Amazon — hai tier, ưu tiên mua dữ liệu.

| Tier | File | Trạng thái |
| --- | --- | --- |
| T1 `vendor:apify` | `vendor.py` | Tier **chính** (quyết định D1). Cần `AMAZON_APIFY_ACTOR` |
| T2 `browser` | `browser.py` | Dự phòng. Cần proxy residential khớp marketplace (G2) |

Thị trường SRS nhắm: DE · FR · IT · ES. NL/BE là sân của Bol.com chứ không phải
Amazon — xem `docs/DEV-Design-Crawl-Engine.md` §2.1.

Amazon KHÔNG công bố số đã bán; chỉ số thay thế là `bestseller_rank` + `review_count`
+ tốc độ tăng review (§2.2).
"""
