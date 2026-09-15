"""Nạp toàn bộ model để `Base.metadata` luôn đầy đủ.

Alembic autogenerate so sánh `Base.metadata` với schema thật trong DB. Model nào
**không** được import ở đây sẽ vắng mặt trong metadata, và autogenerate hiểu nhầm
là bảng đó "đã bị xoá khỏi code" → sinh ra `op.drop_table(...)`.

Nên: **thêm model mới thì thêm một dòng vào đây.**
"""

from app.models.ads import AdSignal
from app.models.ai import AiAnalysisResult, AiProviderKey, MasterPrompt
from app.models.audit_log import ApiAuditLog
from app.models.browser_profile import BrowserProfile
from app.models.crawl_ops import CrawlAttempt, CrawlSourcePolicy
from app.models.ecom import EcomPricePoint, EcomProduct
from app.models.provider import Proxy, ProviderKey
from app.models.results import FacebookAdsResult, GoogleTrendsResult
from app.models.tracking import CrawlWatchlist, ProductMetricSnapshot
from app.models.voc import RedditComment, RedditThread

__all__ = [
    "AdSignal",
    "AiAnalysisResult",
    "AiProviderKey",
    "ApiAuditLog",
    "BrowserProfile",
    "CrawlAttempt",
    "CrawlSourcePolicy",
    "MasterPrompt",
    "EcomPricePoint",
    "EcomProduct",
    "FacebookAdsResult",
    "GoogleTrendsResult",
    "Proxy",
    "CrawlWatchlist",
    "ProductMetricSnapshot",
    "ProviderKey",
    "RedditComment",
    "RedditThread",
]
