from pathlib import Path
import logging

import pytest
from pydantic import ValidationError

from dennis_bot.config import Settings
from dennis_bot.logging_config import SecretRedactionFilter, configure_logging, redact


def test_settings_parse_comma_separated_lists_and_paths() -> None:
    settings = Settings(
        admin_telegram_user_ids="123, 456",
        telegram_sticker_packs="dennis_pack, reactions",
        database_path="data/test.sqlite3",
    )

    assert settings.admin_telegram_user_ids == [123, 456]
    assert settings.telegram_sticker_packs == ["dennis_pack", "reactions"]
    assert settings.database_path == Path("data/test.sqlite3")


def test_settings_tolerate_quoted_env_values(monkeypatch) -> None:
    monkeypatch.setenv("APP_PORT", '"8000"')
    monkeypatch.setenv("TELEGRAM_USE_POLLING", '"false"')
    monkeypatch.setenv("TELEGRAM_BOT_USER_ID", '"8732088288"')
    monkeypatch.setenv("TRUSTED_GROUP_CHAT_ID", '"-5207380593"')
    monkeypatch.setenv("ADMIN_TELEGRAM_USER_IDS", '"5611681048"')
    monkeypatch.setenv("SIMPLEMEM_MAX_SESSION_MESSAGES", '"30"')
    monkeypatch.setenv("OPENAI_BASE_URL", '""')
    monkeypatch.setenv("TELEGRAM_STICKER_PACKS", '""')

    settings = Settings(_env_file=None)

    assert settings.app_port == 8000
    assert settings.telegram_use_polling is False
    assert settings.telegram_bot_user_id == 8732088288
    assert settings.trusted_group_chat_id == -5207380593
    assert settings.admin_telegram_user_ids == [5611681048]
    assert settings.simplemem_max_session_messages == 30
    assert settings.openai_base_url == ""
    assert settings.telegram_sticker_packs == []


def test_runtime_validation_requires_webhook_secret_only_in_webhook_mode() -> None:
    settings = Settings(
        telegram_bot_token="123456789:abcdefghijklmnopqrstuvwxyzABCDE",
        admin_telegram_user_ids="123",
        openai_api_key="sk-test-value",
        simplemem_mcp_url="https://mcp.simplemem.cloud/mcp",
        simplemem_mcp_token="simplemem-token-value",
        telegram_webhook_secret="",
    )

    assert "TELEGRAM_WEBHOOK_SECRET is required for webhook mode" in settings.validate_for_runtime(
        mode="webhook"
    )
    assert "TELEGRAM_WEBHOOK_SECRET is required for webhook mode" not in settings.validate_for_runtime(
        mode="polling"
    )


def test_runtime_validation_requires_public_https_base_url_for_production_webhook() -> None:
    settings = Settings(
        app_env="production",
        base_url="http://localhost:8000",
        telegram_bot_token="123456789:abcdefghijklmnopqrstuvwxyzABCDE",
        admin_telegram_user_ids="123",
        openai_api_key="sk-test-value",
        simplemem_mcp_url="https://mcp.simplemem.cloud/mcp",
        simplemem_mcp_token="simplemem-token-value",
        telegram_webhook_secret="webhook-secret-value",
    )

    assert (
        "BASE_URL must be the public HTTPS Railway URL in production webhook mode"
        in settings.validate_for_runtime(mode="webhook")
    )
    assert (
        "BASE_URL must be the public HTTPS Railway URL in production webhook mode"
        not in settings.validate_for_runtime(mode="polling")
    )


def test_simplemem_session_limit_must_be_positive() -> None:
    with pytest.raises(ValidationError, match="SIMPLEMEM_MAX_SESSION_MESSAGES must be at least 1"):
        Settings(simplemem_max_session_messages=0)


def test_secret_redaction_masks_token_like_values() -> None:
    redacted = redact(
        "telegram token=123456789:abcdefghijklmnopqrstuvwxyzABCDE "
        "and api_key=abcd1234efgh5678"
    )

    assert "123456789:abcdefghijklmnopqrstuvwxyzABCDE" not in redacted
    assert "abcd1234efgh5678" not in redacted
    assert redacted.count("[REDACTED]") == 2


def test_secret_redaction_filter_preserves_percent_formatting() -> None:
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='HTTP Request: %s "%s %d %s" token=%s',
        args=("POST", "HTTP/1.1", 200, "OK", "123456789:abcdefghijklmnopqrstuvwxyzABCDE"),
        exc_info=None,
    )

    assert SecretRedactionFilter().filter(record) is True
    assert record.getMessage() == 'HTTP Request: POST "HTTP/1.1 200 OK" [REDACTED]'


def test_configure_logging_suppresses_http_request_logs() -> None:
    configure_logging("INFO")

    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING
