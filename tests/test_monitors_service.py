from __future__ import annotations

import json

import httpx
import pytest

from dennis_bot.brightdata import BrightDataClient
from dennis_bot.config import Settings
from dennis_bot.monitors.default_monitors import default_dennis_monitors
from dennis_bot.monitors.models import MonitorChange, NormalizedRecord
from dennis_bot.monitors.normalization import stable_hash
from dennis_bot.monitors.repository import InMemoryMonitorRepository
from dennis_bot.monitors.service import MonitorService


class FakeNotifications:
    def __init__(self) -> None:
        self.sent: list[tuple[int | None, str, MonitorChange]] = []

    async def send_monitor_notification(
        self,
        *,
        chat_id: int | None,
        change: MonitorChange,
        message: str,
    ) -> None:
        self.sent.append((chat_id, message, change))


class FakeKnowledge:
    def __init__(self) -> None:
        self.changes: list[MonitorChange] = []

    async def handle_monitor_change(self, change: MonitorChange) -> None:
        self.changes.append(change)


def test_default_instagram_monitor_prefers_posts_dataset() -> None:
    settings = Settings(
        brightdata_instagram_dataset_id_profile="gd_profile",
        brightdata_instagram_dataset_id_posts="gd_posts",
    )

    instagram = next(monitor for monitor in default_dennis_monitors(settings) if monitor.name == "dennis_instagram")

    assert instagram.provider_dataset_id == "gd_posts"
    assert instagram.monitor_type == "instagram_posts"


@pytest.mark.asyncio
async def test_official_site_monitor_dedupes_hashes_and_dispatches() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={
                "body": """
                <html>
                  <head><title>Dennis Toh</title></head>
                  <body><h1>Updated biography</h1><a href="/about">About</a></body>
                </html>
                """
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.brightdata.com",
    ) as http_client:
        service, repo, notifications, knowledge = _service(http_client=http_client)
        first = await service.run_manual("dennis_official_site")
        second = await service.run_manual("dennis_official_site")

    assert calls == 2
    assert len(first) == 1
    assert second == []
    assert len(repo.runs) == 2
    assert repo.runs[0].records_changed == 1
    assert repo.runs[1].records_changed == 0
    assert len(notifications.sent) == 1
    assert "Dennis Toh" in notifications.sent[0][1]
    assert len(knowledge.changes) == 1


@pytest.mark.asyncio
async def test_instagram_monitor_creates_social_activity_and_ignores_duplicate() -> None:
    captured_payloads: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/datasets/v3/scrape":
            captured_payloads.append(json.loads(request.read()))
            return httpx.Response(
                200,
                content=(
                    b'{"post_id":"post_old","content_type":"Post",'
                    b'"url":"https://www.instagram.com/p/old/",'
                    b'"description":"Older rehearsal moment","date_posted":"2026-05-14T01:00:00Z",'
                    b'"likes":12,"num_comments":1}\n'
                    b'{"post_id":"post_new","content_type":"Post",'
                    b'"url":"https://www.instagram.com/p/new/",'
                    b'"description":"Opening night","date_posted":"2026-05-15T01:00:00Z",'
                    b'"likes":50,"num_comments":3}\n'
                    b'{"error":"Post is not available","timestamp":"2026-05-15T01:00:00Z"}\n'
                ),
                headers={"content-type": "application/jsonl"},
            )
        raise AssertionError(f"unexpected request {request.url}")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.brightdata.com",
    ) as http_client:
        service, repo, notifications, knowledge = _service(http_client=http_client)
        first = await service.run_manual("dennis_instagram")
        second = await service.run_manual("dennis_instagram")

    assert len(first) == 1
    assert second == []
    assert captured_payloads[0]["input"] == [
        {
            "url": "https://www.instagram.com/dennistohsg/",
            "num_of_posts": 1,
            "post_type": "Post",
        }
    ]
    assert len(repo.social_items) == 1
    assert repo.social_items[0].activity_type == "post"
    assert repo.social_items[0].external_id == "post_new"
    assert repo.social_items[0].engagement_snapshot == {"likes": 50, "num_comments": 3}
    assert len(notifications.sent) == 1
    assert "I just posted something new on Instagram" in notifications.sent[0][1]
    assert "Opening night" in notifications.sent[0][1]
    assert "https://www.instagram.com/p/new/" in notifications.sent[0][1]
    assert "Detected:" not in notifications.sent[0][1]
    assert len(knowledge.changes) == 1


@pytest.mark.asyncio
async def test_missing_brightdata_key_skips_monitor_without_network() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: pytest.fail("network should not be called")),
        base_url="https://api.brightdata.com",
    ) as http_client:
        service, repo, notifications, knowledge = _service(api_key="", http_client=http_client)
        changes = await service.run_manual("dennis_official_site")

    assert changes == []
    assert repo.runs[0].status == "skipped"
    assert "BRIGHTDATA_API_KEY" in (repo.runs[0].error_message or "")
    assert notifications.sent == []
    assert knowledge.changes == []


@pytest.mark.asyncio
async def test_process_records_uses_existing_dedupe_and_dispatch_path() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: pytest.fail("network should not be called")),
        base_url="https://api.brightdata.com",
    ) as http_client:
        service, repo, notifications, knowledge = _service(http_client=http_client)
        record = NormalizedRecord(
            monitor_name="dennis_official_site",
            source_type="official_site",
            url="https://www.dennistohsg.com/",
            title="Dennis Toh",
            summary="New public update",
            content="New public update",
            content_hash=stable_hash("New public update"),
            permalink="https://www.dennistohsg.com/",
        )

        first = await service.process_records("dennis_official_site", [record])
        second = await service.process_records("dennis_official_site", [record])

    assert len(first) == 1
    assert second == []
    assert len(notifications.sent) == 1
    assert len(knowledge.changes) == 1
    assert repo.states["dennis_official_site"].last_seen_hash == record.content_hash


@pytest.mark.asyncio
async def test_pause_and_resume_monitor_persist_enabled_override() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: pytest.fail("network should not be called")),
        base_url="https://api.brightdata.com",
    ) as http_client:
        service, repo, _, _ = _service(http_client=http_client)

        await service.pause_monitor("dennis_official_site")
        paused = await service.is_monitor_enabled("dennis_official_site")
        paused_run = await service.run_manual("dennis_official_site")
        await service.resume_monitor("dennis_official_site")
        resumed = await service.is_monitor_enabled("dennis_official_site")

    assert paused is False
    assert paused_run == []
    assert resumed is True
    assert repo.enabled_overrides["dennis_official_site"] is True


def _service(
    *,
    api_key: str = "token",
    http_client: httpx.AsyncClient,
) -> tuple[MonitorService, InMemoryMonitorRepository, FakeNotifications, FakeKnowledge]:
    monitors = default_dennis_monitors()
    for monitor in monitors:
        monitor.target_chat_id = -100123
        if monitor.name == "dennis_instagram":
            monitor.provider_dataset_id = "gd_instagram"
            monitor.monitor_type = "instagram_posts"
    repo = InMemoryMonitorRepository()
    notifications = FakeNotifications()
    knowledge = FakeKnowledge()
    service = MonitorService(
        brightdata_client=BrightDataClient(
            api_key=api_key,
            web_unlocker_zone="web_unlocker1",
            http_client=http_client,
        ),
        repository=repo,
        monitors=monitors,
        notification_service=notifications,
        knowledge_update_service=knowledge,
    )
    return service, repo, notifications, knowledge
