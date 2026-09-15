from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Comma-separated keys: SERPAPI_KEYS=key1,key2,key3
    serpapi_keys: list[str] = []
    openrouter_api_keys: list[str] = []
    apify_token: str = ""

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/crawl_ecomerce"
    app_env: str = "development"
    app_debug: bool = False

    # CloakBrowser — cloakserve CDP endpoint
    cloak_browser_url: str = "http://localhost:9222"
    cloak_license_key: str = ""

    @field_validator("serpapi_keys", "openrouter_api_keys", mode="before")
    @classmethod
    def parse_comma_separated(cls, v):
        if isinstance(v, str):
            return [k.strip() for k in v.split(",") if k.strip()]
        return v or []


settings = Settings()
