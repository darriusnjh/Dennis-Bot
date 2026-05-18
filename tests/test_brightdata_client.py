from __future__ import annotations

import httpx
import pytest

from dennis_bot.brightdata import BrightDataClient


@pytest.mark.asyncio
async def test_web_unlocker_request_uses_expected_endpoint_and_payload() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers["authorization"]
        captured["payload"] = request.read()
        return httpx.Response(
            200,
            json={
                "request_id": "req_123",
                "status_code": 200,
                "body": "<html><title>Dennis</title><p>Updated bio</p></html>",
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.brightdata.com",
    ) as http_client:
        client = BrightDataClient(
            api_key="token",
            web_unlocker_zone="web_unlocker1",
            http_client=http_client,
        )
        snapshot = await client.fetch_web_unlocker("https://www.dennistohsg.com/")

    assert captured["url"] == "https://api.brightdata.com/request"
    assert captured["auth"] == "Bearer token"
    assert b'"zone":"web_unlocker1"' in captured["payload"]
    assert b'"data_format":"markdown"' in captured["payload"]
    assert snapshot.records[0]["body"].startswith("<html>")
    assert snapshot.metadata.provider_request_id == "req_123"
    assert snapshot.metadata.response_record_count == 1


@pytest.mark.asyncio
async def test_dataset_sync_triggers_polls_and_downloads_snapshot() -> None:
    calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path == "/datasets/v3/trigger":
            return httpx.Response(200, json={"snapshot_id": "snap_1"})
        if request.url.path == "/datasets/v3/progress/snap_1":
            return httpx.Response(200, json={"status": "ready"})
        if request.url.path == "/datasets/v3/snapshot/snap_1":
            return httpx.Response(200, json=[{"id": "post_1", "caption": "new post"}])
        raise AssertionError(f"unexpected request {request.url}")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.brightdata.com",
    ) as http_client:
        client = BrightDataClient(api_key="token", http_client=http_client)
        snapshot = await client.request_dataset_sync(
            dataset_id="gd_instagram",
            inputs=[{"url": "https://www.instagram.com/dennistohsg/"}],
            poll_interval_seconds=0,
        )

    assert calls == [
        "https://api.brightdata.com/datasets/v3/trigger?dataset_id=gd_instagram",
        "https://api.brightdata.com/datasets/v3/progress/snap_1",
        "https://api.brightdata.com/datasets/v3/snapshot/snap_1?format=json",
    ]
    assert snapshot.records == [{"id": "post_1", "caption": "new post"}]
    assert snapshot.metadata.provider_snapshot_id == "snap_1"
    assert snapshot.metadata.status == "succeeded"


@pytest.mark.asyncio
async def test_scrape_discover_by_url_parses_jsonl_response() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["payload"] = request.read()
        return httpx.Response(
            200,
            content=(
                b'{"post_id":"post_1","description":"new post","url":"https://instagram.test/p/1"}\n'
                b'{"post_id":"post_2","description":"second post","url":"https://instagram.test/p/2"}\n'
            ),
            headers={"content-type": "application/jsonl; charset=utf-8"},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.brightdata.com",
    ) as http_client:
        client = BrightDataClient(api_key="token", http_client=http_client)
        snapshot = await client.scrape_dataset_discover_by_url_sync(
            dataset_id="gd_posts",
            inputs=[{"url": "https://www.instagram.com/dennistohsg/", "num_of_posts": 10}],
        )

    assert captured["url"] == (
        "https://api.brightdata.com/datasets/v3/scrape?"
        "dataset_id=gd_posts&notify=false&include_errors=true&type=discover_new&discover_by=url"
    )
    assert b'"input":[{"url":"https://www.instagram.com/dennistohsg/","num_of_posts":10}]' in captured[
        "payload"
    ]
    assert snapshot.records == [
        {"post_id": "post_1", "description": "new post", "url": "https://instagram.test/p/1"},
        {"post_id": "post_2", "description": "second post", "url": "https://instagram.test/p/2"},
    ]
    assert snapshot.metadata.endpoint == "/datasets/v3/scrape"
    assert snapshot.metadata.request_mode == "sync"
    assert snapshot.metadata.response_record_count == 2


@pytest.mark.asyncio
async def test_scrape_discover_by_url_can_poll_returned_snapshot() -> None:
    calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path == "/datasets/v3/scrape":
            return httpx.Response(202, json={"snapshot_id": "snap_posts"})
        if request.url.path == "/datasets/v3/progress/snap_posts":
            return httpx.Response(200, json={"status": "ready"})
        if request.url.path == "/datasets/v3/snapshot/snap_posts":
            return httpx.Response(200, json=[{"post_id": "post_1"}])
        raise AssertionError(f"unexpected request {request.url}")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.brightdata.com",
    ) as http_client:
        client = BrightDataClient(api_key="token", http_client=http_client)
        snapshot = await client.scrape_dataset_discover_by_url_sync(
            dataset_id="gd_posts",
            inputs=[{"url": "https://www.instagram.com/dennistohsg/"}],
            poll_interval_seconds=0,
        )

    assert calls == [
        "https://api.brightdata.com/datasets/v3/scrape?"
        "dataset_id=gd_posts&notify=false&include_errors=true&type=discover_new&discover_by=url",
        "https://api.brightdata.com/datasets/v3/progress/snap_posts",
        "https://api.brightdata.com/datasets/v3/snapshot/snap_posts?format=json",
    ]
    assert snapshot.records == [{"post_id": "post_1"}]
    assert snapshot.metadata.provider_snapshot_id == "snap_posts"
    assert snapshot.metadata.status == "succeeded"
