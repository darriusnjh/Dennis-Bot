from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_OPENROUTER_MODEL = "x-ai/grok-4.3"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        enable_decoding=False,
    )

    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    base_url: str = "http://localhost:8000"
    log_level: str = "INFO"

    telegram_bot_token: str = ""
    telegram_bot_username: str | None = None
    telegram_bot_user_id: int | None = None
    telegram_webhook_secret: str = ""
    telegram_use_polling: bool = False
    admin_telegram_user_ids: list[int] = Field(default_factory=list)
    trusted_group_chat_id: int | None = None

    openrouter_api_key: str = ""
    openrouter_base_url: str | None = ""
    openrouter_model: str = ""
    openai_api_key: str = ""
    openai_base_url: str | None = None
    openai_model: str = "gpt-4.1-mini"

    simplemem_mcp_url: str = "https://mcp.simplemem.cloud/mcp"
    simplemem_mcp_token: str = ""
    simplemem_tenant_id: str = "dennis-bot-global"
    simplemem_project: str = "dennis-bot"
    simplemem_max_session_messages: int = 30

    database_path: Path = Path("data/dennis_bot.sqlite3")

    brightdata_api_key: str = ""
    brightdata_web_unlocker_zone: str = ""
    brightdata_instagram_dataset_id_profile: str = ""
    brightdata_instagram_dataset_id_posts: str = ""
    brightdata_instagram_dataset_id_reels: str = ""
    brightdata_instagram_dataset_id_comments: str = ""
    brightdata_webhook_secret: str = ""

    telegram_sticker_packs: list[str] = Field(default_factory=list)

    @field_validator("*", mode="before")
    @classmethod
    def strip_env_quotes(cls, value: object) -> object:
        return _strip_optional_env_quotes(value)

    @field_validator("admin_telegram_user_ids", mode="before")
    @classmethod
    def parse_admin_ids(cls, value: object) -> list[int]:
        return _parse_int_list(value)

    @field_validator("telegram_sticker_packs", mode="before")
    @classmethod
    def parse_sticker_packs(cls, value: object) -> list[str]:
        value = _strip_optional_env_quotes(value)
        if value is None or value == "":
            return []
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return []

    @field_validator("telegram_bot_user_id", "trusted_group_chat_id", mode="before")
    @classmethod
    def parse_optional_int(cls, value: object) -> int | None:
        value = _strip_optional_env_quotes(value)
        if value is None or value == "":
            return None
        return int(value)

    @field_validator("simplemem_max_session_messages")
    @classmethod
    def validate_session_limit(cls, value: int) -> int:
        if value < 1:
            raise ValueError("SIMPLEMEM_MAX_SESSION_MESSAGES must be at least 1")
        return value

    @property
    def telegram_webhook_path(self) -> str:
        return "/webhooks/telegram"

    @property
    def telegram_webhook_url(self) -> str:
        return f"{self.base_url.rstrip('/')}{self.telegram_webhook_path}"

    @property
    def brightdata_webhook_path(self) -> str:
        return "/webhooks/brightdata"

    @property
    def llm_api_key(self) -> str:
        return _first_non_blank(self.openrouter_api_key, self.openai_api_key) or ""

    @property
    def llm_base_url(self) -> str | None:
        if self._uses_openrouter_config:
            return _first_non_blank(self.openrouter_base_url) or DEFAULT_OPENROUTER_BASE_URL
        return _first_non_blank(self.openai_base_url)

    @property
    def llm_model(self) -> str:
        if self._uses_openrouter_config:
            return _first_non_blank(self.openrouter_model) or DEFAULT_OPENROUTER_MODEL
        return _first_non_blank(self.openai_model) or "gpt-4.1-mini"

    @property
    def _uses_openrouter_config(self) -> bool:
        api_key = self.llm_api_key
        return bool(
            _first_non_blank(self.openrouter_api_key)
            or api_key.startswith("sk-or-")
            or "openrouter.ai" in (self.openai_base_url or "").lower()
        )

    def validate_for_runtime(self, mode: Literal["webhook", "polling"] | None = None) -> list[str]:
        errors: list[str] = []
        if not self.telegram_bot_token:
            errors.append("TELEGRAM_BOT_TOKEN is required")
        if not self.admin_telegram_user_ids:
            errors.append("ADMIN_TELEGRAM_USER_IDS should include at least one Telegram user ID")
        if not self.llm_api_key:
            errors.append("OPENROUTER_API_KEY or OPENAI_API_KEY is required for LLM responses")
        if not self.simplemem_mcp_url:
            errors.append("SIMPLEMEM_MCP_URL is required")
        if not self.simplemem_mcp_token:
            errors.append("SIMPLEMEM_MCP_TOKEN is required for SimpleMem MCP")
        if mode == "webhook" and not self.telegram_webhook_secret:
            errors.append("TELEGRAM_WEBHOOK_SECRET is required for webhook mode")
        if mode == "webhook" and self.app_env == "production":
            parsed_base_url = urlparse(self.base_url)
            local_hosts = {"localhost", "127.0.0.1", "0.0.0.0"}
            if (
                parsed_base_url.scheme != "https"
                or not parsed_base_url.netloc
                or (parsed_base_url.hostname or "").lower() in local_hosts
            ):
                errors.append(
                    "BASE_URL must be the public HTTPS Railway URL in production webhook mode"
                )
        return errors


def _parse_int_list(value: object) -> list[int]:
    value = _strip_optional_env_quotes(value)
    if value is None or value == "":
        return []
    if isinstance(value, int):
        return [value]
    if isinstance(value, str):
        items = [item.strip() for item in value.split(",") if item.strip()]
        parsed: list[int] = []
        for item in items:
            try:
                parsed.append(int(item))
            except ValueError:
                continue
        return parsed
    if isinstance(value, list):
        parsed = []
        for item in value:
            try:
                parsed.append(int(item))
            except (TypeError, ValueError):
                continue
        return parsed
    return []


def _strip_optional_env_quotes(value: object) -> object:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {"'", '"'}:
        return stripped[1:-1]
    return value


def _first_non_blank(*values: object) -> str | None:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


@lru_cache
def get_settings() -> Settings:
    return Settings()
