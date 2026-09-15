"""Reddit VOC — nguồn duy nhất hiện có ĐÚNG hai tier chạy thật.

T0 `official` (OAuth app-only, miễn phí) → T1 `vendor:apify` (trả phí).

Trước G1, việc chuyển tầng do `reddit_client.collect_voc` tự lo bằng tay. Giờ nó là
việc của `crawl/engine.py` như mọi nguồn khác — hai cơ chế fallback song song là
thứ chắc chắn sẽ lệch nhau theo thời gian.
"""
