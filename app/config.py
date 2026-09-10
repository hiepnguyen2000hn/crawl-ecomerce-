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

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/crawl_ecomerce"
    redis_url: str = "redis://localhost:6379/0"
    job_ttl_seconds: int = 86400
    app_env: str = "development"
    app_debug: bool = False

    @property
    def serpapi_keys(self) -> list[str]:
        return [k.strip() for k in self.serpapi_keys_raw.split(",") if k.strip()]

    @property
    def openrouter_api_keys(self) -> list[str]:
        return [k.strip() for k in self.openrouter_api_keys_raw.split(",") if k.strip()]


settings = Settings()
