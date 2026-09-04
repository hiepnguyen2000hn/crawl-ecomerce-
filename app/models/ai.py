from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base

DEFAULT_SYSTEM_PROMPT = """You are an expert e-commerce data extractor.
Your task is to analyze web page content and extract product pricing information accurately.
Always return valid JSON. Never include markdown code blocks in your response."""

DEFAULT_USER_TEMPLATE = """Analyze the following web page content from: {url}

Extract ALL product pricing information you can find. Return a JSON object with this structure:
{{
  "products": [
    {{
      "name": "product name",
      "current_price": 0.0,
      "original_price": 0.0,
      "currency": "VND",
      "discount_percent": 0,
      "sku": "optional sku",
      "url": "optional product url"
    }}
  ],
  "currency_detected": "VND",
  "total_products_found": 0,
  "notes": "any relevant notes"
}}

Web page content:
{content}"""


class MasterPrompt(Base):
    __tablename__ = "master_prompts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    system_prompt: Mapped[str] = mapped_column(Text)
    user_template: Mapped[str] = mapped_column(Text)  # {url} and {content} placeholders
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AiProviderKey(Base):
    __tablename__ = "ai_provider_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(50), default="openrouter")  # future: other providers
    label: Mapped[str] = mapped_column(String(100))
    api_key: Mapped[str] = mapped_column(String(255), unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    usage_count: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AiAnalysisResult(Base):
    __tablename__ = "ai_analysis_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(UUID(as_uuid=False), index=True)
    target_url: Mapped[str] = mapped_column(Text, index=True)
    model_used: Mapped[str] = mapped_column(String(150))
    prompt_name: Mapped[str] = mapped_column(String(100))    # which master prompt was used
    extracted_data: Mapped[dict] = mapped_column(JSONB)      # parsed JSON from AI
    raw_response: Mapped[str] = mapped_column(Text)          # raw text from AI
    tokens_prompt: Mapped[int] = mapped_column(Integer, default=0)
    tokens_completion: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(20), default="openrouter")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
