"""1688 — nguồn hàng cho SRS Bước 4. Hai tier, ưu tiên mua dữ liệu.

| Tier | File | Trạng thái |
| --- | --- | --- |
| T1 `vendor:aggregator` | `vendor.py` | Tier **chính**. Cần `ALIBABA_AGGREGATOR_*` |
| T2 `browser` | `browser.py` | Dự phòng. Cần proxy residential **Trung Quốc đại lục** (G2) |

`docs/DEV-Design-Crawl-Engine.md` gọi đây là nguồn **nên mua dứt khoát nhất**: cổng
chính thức đòi pháp nhân + xác thực thực danh tại TQ, còn tự scrape thì vướng đăng
nhập, slider captcha và device token `x5sec`.

Chỉ tier vendor phục vụ được `SEARCH_IMAGE` (tìm nguồn hàng bằng ảnh — SRS Bước 4.1),
vì tự scrape phải mô phỏng cả luồng upload ảnh sau lớp đăng nhập.
"""
