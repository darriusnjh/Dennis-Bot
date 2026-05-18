from fastapi.testclient import TestClient
import pytest

from dennis_bot.app import create_app
from dennis_bot.config import Settings
from dennis_bot.brightdata import BrightDataClient
from dennis_bot.monitors.default_monitors import default_dennis_monitors
from dennis_bot.monitors.health import (
    brightdata_config_health,
    collect_subsystem_health,
    monitor_count_health,
    scheduler_health,
    simplemem_health,
)
from dennis_bot.monitors.repository import InMemoryMonitorRepository
from dennis_bot.monitors.service import MonitorService
from dennis_bot.scheduler.service import MonitorScheduler


def test_health_endpoint_shape_for_valid_webhook_config() -> None:
    settings = Settings(
        telegram_bot_token="123456789:abcdefghijklmnopqrstuvwxyzABCDE",
        telegram_webhook_secret="webhook-secret-value",
        admin_telegram_user_ids="123",
        openai_api_key="sk-test-value",
        simplemem_mcp_url="https://mcp.simplemem.cloud/mcp",
        simplemem_mcp_token="simplemem-token-value",
        simplemem_tenant_id="dennis-bot-global",
        simplemem_project="dennis-bot",
        telegram_use_polling=False,
    )
    client = TestClient(create_app(settings))

    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["mode"] == "webhook"
    assert body["config_errors"] == []
    assert body["simplemem_project"] == "dennis-bot"
    assert body["simplemem_tenant_id"] == "dennis-bot-global"
    assert any(item["subsystem"] == "simplemem" for item in body["subsystems"])
    assert any(item["subsystem"] == "brightdata" for item in body["subsystems"])
    assert any(item["subsystem"] == "telegram_webhook" for item in body["subsystems"])


def test_health_endpoint_reports_missing_config_without_secret_values() -> None:
    client = TestClient(create_app(Settings(_env_file=None)))

    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["mode"] == "webhook"
    assert "TELEGRAM_BOT_TOKEN is required" in body["config_errors"]
    assert "OPENAI_API_KEY is required for LLM responses" in body["config_errors"]
    assert "SIMPLEMEM_MCP_TOKEN is required for SimpleMem MCP" in body["config_errors"]
    assert "TELEGRAM_WEBHOOK_SECRET is required for webhook mode" in body["config_errors"]
    assert all("token=" not in error.lower() for error in body["config_errors"])
    webhook = next(item for item in body["subsystems"] if item["subsystem"] == "telegram_webhook")
    assert webhook["ok"] is False
    assert "TELEGRAM_BOT_TOKEN is required" in webhook["config_errors"]


def test_startup_rejects_directory_database_path(tmp_path) -> None:
    settings = Settings(_env_file=None, database_path=tmp_path)

    with pytest.raises(RuntimeError, match="DATABASE_PATH must point to a SQLite file"):
        with TestClient(create_app(settings)):
            pass


class FakeSimpleMem:
    async def health_check(self) -> dict[str, object]:
        return {"ok": True, "project": "dennis-bot"}


@pytest.mark.asyncio
async def test_health_helpers_report_subsystem_state() -> None:
    settings = Settings(
        brightdata_api_key="token",
        brightdata_web_unlocker_zone="zone",
        brightdata_webhook_secret="secret",
    )
    service = MonitorService(
        brightdata_client=BrightDataClient(api_key="token", web_unlocker_zone="zone"),
        repository=InMemoryMonitorRepository(),
        monitors=default_dennis_monitors(),
    )
    scheduler = MonitorScheduler(monitor_service=service)

    brightdata = brightdata_config_health(settings)
    simplemem = await simplemem_health(FakeSimpleMem())
    monitor_count = await monitor_count_health(service)
    scheduler_state = scheduler_health(scheduler)
    aggregate = await collect_subsystem_health(
        [
            lambda: brightdata,
            lambda: simplemem,
            lambda: monitor_count,
            lambda: scheduler_state,
        ]
    )

    assert brightdata["ok"] is True
    assert simplemem["ok"] is True
    assert monitor_count["configured"] == 3
    assert scheduler_state["running"] is False
    assert aggregate["ok"] is False


def test_dockerfile_copies_migrations_into_image() -> None:
    dockerfile = open("Dockerfile", encoding="utf-8").read()

    assert "COPY migrations ./migrations" in dockerfile
    assert "gosu" in dockerfile
    assert 'ENTRYPOINT ["/app/scripts/docker-entrypoint.sh"]' in dockerfile


def test_docker_entrypoint_falls_back_when_volume_chown_fails() -> None:
    entrypoint = open("scripts/docker-entrypoint.sh", encoding="utf-8").read()

    assert "could not chown" in entrypoint
    assert 'exec "$@"' in entrypoint
