"""Mỗi nguồn một gói, mỗi tier một adapter — tất cả tuân theo `app.crawl.contracts.SourceAdapter`.

Xem docs/DEV-Design-Crawl-Engine.md §7.1. Ở G1 chỉ có gói `fake/` (đọc fixture,
không gọi mạng) để chứng minh `engine.py` chạy đúng trước khi có adapter thật nào.
"""
