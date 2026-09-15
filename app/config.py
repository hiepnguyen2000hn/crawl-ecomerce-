from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Comma-separated keys: SERPAPI_KEYS=key1,key2,key3
    # Field kiểu list[str] bị pydantic-settings 2.5.2 parse như JSON trước khi tới validator
    # tùy chỉnh — "key1,key2,key3" không phải JSON hợp lệ nên app crash ngay lúc khởi động.
    # Giữ raw string ở đây, tách list qua property bên dưới để tránh bước JSON-decode đó.
    serpapi_keys_raw: str = Field(default="", alias="SERPAPI_KEYS")
    openrouter_api_keys_raw: str = Field(default="", alias="OPENROUTER_API_KEYS")
    apify_token: str = ""
    apify_max_cost_per_run_usd: float = 0.50
    """Trần chi phí MỘT lần chạy actor. Vượt là hủy run giữa chừng.

    Không phải phòng xa: một actor được gọi với maxItems=10 đã trả 2.693 item và
    tốn $3.98. `maxItems` là gợi ý, trần này mới là ràng buộc."""

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/crawl_ecomerce"
    redis_url: str = "redis://localhost:6379/0"
    job_ttl_seconds: int = 86400
    app_env: str = "development"
    app_debug: bool = False

    # ── Nhịp crawl (áp cho các connector tự fetch: Shopify, Bol.com, Reddit) ──
    # Bắt đầu chậm rồi nới ra rẻ hơn nhiều so với bắt đầu nhanh rồi bị chặn —
    # lệnh cấm thường theo dải IP nên mất cả pool chứ không mất một IP.
    crawl_min_delay_ms: int = 1500
    """Khoảng cách tối thiểu giữa 2 request tới cùng một host."""
    crawl_jitter_ms: int = 500
    """Ngẫu nhiên cộng thêm — nhịp đều tăm tắp là tín hiệu bot."""
    crawl_timeout_s: float = 30.0
    crawl_user_agent: str = ""
    """Bỏ trống thì dùng UA Chrome mặc định trong app/crawl/http.py."""

    # ── Reddit (SRS Bước 1.4) ────────────────────────────────────────────────
    # Tạo app kiểu "script" tại https://www.reddit.com/prefs/apps
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "levelup-research/0.1 by /u/levelup_bot"
    reddit_provider: str = "auto"
    """'auto' — dùng OAuth nếu có credential, không thì rơi xuống Apify.
    'oauth' / 'apify' — ép dùng đúng một tầng (để test hoặc để chặn chi phí)."""
    """Reddit BẮT BUỘC UA mô tả thật dạng `platform:app-id:version (by /u/user)`;
    UA chung chung bị chặn thẳng."""

    # ── CloakBrowser — cloakserve CDP endpoint (T2 tự scrape) ────────────────
    cloak_browser_url: str = "http://localhost:9222"
    cloak_license_key: str = ""

    # ── Amazon · 1688 · Taobao — tier T1 (mua dữ liệu) ───────────────────────
    # Ba nguồn này ưu tiên vendor, tự scrape chỉ là dự phòng (quyết định D1,
    # docs/DEV-Design-Crawl-Engine.md). Để trống thì `is_available()` của adapter
    # vendor trả False và engine bỏ qua tier đó — không phải lỗi cấu hình.
    amazon_apify_actor: str = ""
    """Actor Apify cho Amazon, ví dụ 'junglee~amazon-crawler'. Để trong env chứ không
    hard-code vì docs §2 yêu cầu chạy thử 100 request thật với vài vendor rồi mới ký
    hợp đồng năm — so sánh được thì phải đổi được bằng một dòng env."""

    alibaba_aggregator_base: str = ""
    """Ví dụ 'https://api.onebound.cn/{platform}/api_call.php'. Chỗ `{platform}` được
    thay bằng '1688' hoặc 'taobao' — một biến env phục vụ cả hai nguồn."""
    alibaba_aggregator_key: str = ""
    alibaba_aggregator_secret: str = ""

    @property
    def serpapi_keys(self) -> list[str]:
        return [k.strip() for k in self.serpapi_keys_raw.split(",") if k.strip()]

    @property
    def openrouter_api_keys(self) -> list[str]:
        return [k.strip() for k in self.openrouter_api_keys_raw.split(",") if k.strip()]


settings = Settings()
