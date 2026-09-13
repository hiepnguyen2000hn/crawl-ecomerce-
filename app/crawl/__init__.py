"""Khung thu thập dùng chung — không biết gì về nguồn cụ thể.

Mọi connector (Shopify, Bol.com, Reddit, ...) đi qua cùng một bộ:
  - `outcomes`  : phân loại kết quả → quyết định có retry hay không
  - `http`      : gọi HTTP có nhịp, có User-Agent, có phân loại sẵn
  - `normalize` : chuẩn hoá tiền tệ về integer minor unit

Xem thiết kế đầy đủ: docs/DEV-Design-Crawl-Engine.md
"""
