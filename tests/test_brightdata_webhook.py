from __future__ import annotations

import httpx
import pytest

from dennis_bot.brightdata import BrightDataClient
from dennis_bot.webhooks.brightdata import (
    BrightDataWebhookError,
    normalize_brightdata_webhook_payload,
    process_brightdata_webhook,
    validate_brightdata_webhook_secret,
)
from dennis_bot.monitors.default_monitors import default_dennis_monitors
from dennis_bot.monitors.repository import InMemoryMonitorRepository
from dennis_bot.monitors.service import MonitorService


def test_brightdata_webhook_secret_validation_is_fail_closed() -> None:
    validate_brightdata_webhook_secret("expected", "expected")

    with pytest.raises(BrightDataWebhookError):
        validate_brightdata_webhook_secret("", "expected")

    with pytest.raises(BrightDataWebhookError):
        validate_brightdata_webhook_secret("expected", "wrong")


def test_brightdata_webhook_payload_normalizes_snapshot_metadata() -> None:
    payload = {
        "monitor_name": "dennis_instagram",
        "snapshot_id": "snap_123",
        "status": "completed",
        "records": [{"id": "post_1", "caption": "New reel"}],
    }

    normalized = normalize_brightdata_webhook_payload(payload)

    assert normalized.monitor_name == "dennis_instagram"
    assert normalized.snapshot.metadata.request_mode == "webhook"
    assert normalized.snapshot.metadata.status == "succeeded"
    assert normalized.snapshot.metadata.provider_snapshot_id == "snap_123"
    assert normalized.snapshot.records == [{"id": "post_1", "caption": "New reel"}]


@pytest.mark.asyncio
async def test_process_brightdata_webhook_creates_webhook_run_and_dedupes() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: pytest.fail("network should not be called")),
        base_url="https://api.brightdata.com",
    ) as http_client:
        repo = InMemoryMonitorRepository()
        monitors = default_dennis_monitors()
        service = MonitorService(
            brightdata_client=BrightDataClient(
                api_key="token",
                web_unlocker_zone="zone",
                http_client=http_client,
            ),
            repository=repo,
            monitors=monitors,
        )
        payload = {
            "monitor_name": "dennis_instagram",
            "snapshot_id": "snap_webhook",
            "records": [
                {
                    "id": "post_1",
                    "type": "post",
                    "url": "https://www.instagram.com/p/abc/",
                    "caption": "Opening night",
                }
            ],
        }

        first = await process_brightdata_webhook(
            payload=payload,
            supplied_secret="secret",
            expected_secret="secret",
            monitor_service=service,
        )
        second = await process_brightdata_webhook(
            payload=payload,
            supplied_secret="secret",
            expected_secret="secret",
            monitor_service=service,
        )

    assert len(first) == 1
    assert second == []
    assert len(repo.runs) == 2
    assert repo.runs[0].request_mode == "webhook"
    assert repo.runs[0].provider_snapshot_id == "snap_webhook"
    assert repo.runs[0].records_changed == 1
    assert repo.runs[1].records_changed == 0
