"""Taobao — nguồn hàng cho SRS Bước 4. Hai tier, ưu tiên mua dữ liệu.

| Tier | File | Trạng thái |
| --- | --- | --- |
| T1 `vendor:aggregator` | `vendor.py` | Tier **chính**. Dùng chung key với 1688 |
| T2 `browser` | `browser.py` | Dự phòng. **Khó nhất nhóm** — bắt đăng nhập sớm |

Taobao có công bố số người đã mua (`view_sales`, dạng "123人付款" / "1.2万人付款"),
nên đây là một trong số ít nguồn điền được `sales_volume` — xem
`docs/DEV-Design-Crawl-Engine.md` §2.2.
"""
