from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    serpapi_key: str
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/crawl_ecomerce"
    app_env: str = "development"
    app_debug: bool = False


settings = Settings()
