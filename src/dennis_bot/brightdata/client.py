from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

import httpx

from dennis_bot.brightdata.models import BrightDataRunMetadata, BrightDataSnapshot


class BrightDataClientError(RuntimeError):
    """Raised when Bright Data configuration or provider responses are invalid."""


class BrightDataClient:
    """Small async client for Bright Data Unlocker and Dataset APIs."""

    def __init__(
        self,
        *,
        api_key: str,
        web_unlocker_zone: str = "",
        base_url: str = "https://api.brightdata.com",
        http_client: httpx.AsyncClient | None = None,
        timeout: float = 60.0,
        max_retries: int = 2,
    ) -> None:
        self.api_key = api_key
        self.web_unlocker_zone = web_unlocker_zone
        self.base_url = base_url.rstrip("/")
        self._http_client = http_client
        self.timeout = timeout
        self.max_retries = max_retries

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    async def fetch_web_unlocker(
        self,
        url: str,
        *,
        data_format: str = "markdown",
        country: str | None = None,
    ) -> BrightDataSnapshot:
        if not self.api_key:
            raise BrightDataClientError("BRIGHTDATA_API_KEY is required")
        if not self.web_unlocker_zone:
            raise BrightDataClientError("BRIGHTDATA_WEB_UNLOCKER_ZONE is required")

        endpoint = "/request"
        started_at = _utc_now()
        payload: dict[str, Any] = {
            "zone": self.web_unlocker_zone,
            "url": url,
            "format": "json",
            "method": "GET",
            "data_format": data_format,
        }
        if country:
            payload["country"] = country

        response = await self._request("POST", endpoint, json=payload)
        completed_at = _utc_now()
        body = _extract_unlocker_body(response)
        record = {
            "url": url,
            "body": body,
            "status_code": response.get("status_code"),
            "headers": response.get("headers", {}),
        }
        metadata = BrightDataRunMetadata(
            endpoint=endpoint,
            request_mode="sync",
            status="succeeded",
            started_at=started_at,
            completed_at=completed_at,
            provider_request_id=_first_string(response, "request_id", "id"),
            response_record_count=1,
            cost_record_count=1,
        )
        return BrightDataSnapshot(records=[record], metadata=metadata, raw_response=response)

    async def request_dataset_sync(
        self,
        *,
        dataset_id: str,
        inputs: Iterable[dict[str, Any]],
        poll_interval_seconds: float = 2.0,
        timeout_seconds: float = 120.0,
    ) -> BrightDataSnapshot:
        snapshot = await self.trigger_dataset(dataset_id=dataset_id, inputs=inputs)
        snapshot_id = snapshot.metadata.provider_snapshot_id
        if not snapshot_id:
            raise BrightDataClientError("Bright Data trigger response did not include snapshot_id")

        return await self._wait_for_snapshot(
            snapshot,
            poll_interval_seconds=poll_interval_seconds,
            timeout_seconds=timeout_seconds,
        )

    async def scrape_dataset_discover_by_url_sync(
        self,
        *,
        dataset_id: str,
        inputs: Iterable[dict[str, Any]],
        poll_interval_seconds: float = 2.0,
        timeout_seconds: float = 120.0,
    ) -> BrightDataSnapshot:
        if not self.api_key:
            raise BrightDataClientError("BRIGHTDATA_API_KEY is required")
        if not dataset_id:
            raise BrightDataClientError("Bright Data dataset_id is required")

        endpoint = "/datasets/v3/scrape"
        started_at = _utc_now()
        response = await self._request(
            "POST",
            endpoint,
            params={
                "dataset_id": dataset_id,
                "notify": "false",
                "include_errors": "true",
                "type": "discover_new",
                "discover_by": "url",
            },
            json={"input": list(inputs)},
        )
        metadata = BrightDataRunMetadata(
            endpoint=endpoint,
            request_mode="sync",
            status="succeeded",
            started_at=started_at,
        )

        if isinstance(response, dict):
            snapshot_id = _first_string(response, "snapshot_id", "id")
            if snapshot_id:
                metadata.request_mode = "async"
                metadata.status = "queued"
                metadata.provider_snapshot_id = snapshot_id
                snapshot = BrightDataSnapshot(records=[], metadata=metadata, raw_response=response)
                return await self._wait_for_snapshot(
                    snapshot,
                    poll_interval_seconds=poll_interval_seconds,
                    timeout_seconds=timeout_seconds,
                )

        records = _records_from_response(response)
        metadata.completed_at = _utc_now()
        metadata.response_record_count = len(records)
        return BrightDataSnapshot(records=records, metadata=metadata, raw_response=response)

    async def _wait_for_snapshot(
        self,
        snapshot: BrightDataSnapshot,
        *,
        poll_interval_seconds: float,
        timeout_seconds: float,
    ) -> BrightDataSnapshot:
        snapshot_id = snapshot.metadata.provider_snapshot_id
        if not snapshot_id:
            raise BrightDataClientError("Bright Data snapshot_id is required")

        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while True:
            progress = await self.get_snapshot_progress(snapshot_id)
            status = str(progress.get("status", "")).lower()
            if status in {"ready", "done", "completed", "success"}:
                records = await self.download_snapshot(snapshot_id)
                snapshot.metadata.status = "succeeded"
                snapshot.metadata.completed_at = _utc_now()
                snapshot.metadata.response_record_count = len(records)
                snapshot.records = records
                snapshot.raw_response = records
                return snapshot
            if status in {"failed", "error", "canceled", "cancelled"}:
                snapshot.metadata.status = "failed"
                snapshot.metadata.completed_at = _utc_now()
                snapshot.metadata.error_message = str(progress.get("error") or progress)
                raise BrightDataClientError(snapshot.metadata.error_message)
            if asyncio.get_running_loop().time() >= deadline:
                snapshot.metadata.status = "failed"
                snapshot.metadata.completed_at = _utc_now()
                snapshot.metadata.error_message = "Timed out waiting for Bright Data snapshot"
                raise BrightDataClientError(snapshot.metadata.error_message)
            await asyncio.sleep(poll_interval_seconds)

    async def trigger_dataset(
        self,
        *,
        dataset_id: str,
        inputs: Iterable[dict[str, Any]],
    ) -> BrightDataSnapshot:
        if not self.api_key:
            raise BrightDataClientError("BRIGHTDATA_API_KEY is required")
        if not dataset_id:
            raise BrightDataClientError("Bright Data dataset_id is required")

        endpoint = "/datasets/v3/trigger"
        started_at = _utc_now()
        response = await self._request(
            "POST",
            endpoint,
            params={"dataset_id": dataset_id},
            json=list(inputs),
        )
        snapshot_id = _first_string(response, "snapshot_id", "id")
        metadata = BrightDataRunMetadata(
            endpoint=endpoint,
            request_mode="async",
            status="queued",
            started_at=started_at,
            provider_snapshot_id=snapshot_id,
            response_record_count=0,
        )
        return BrightDataSnapshot(records=[], metadata=metadata, raw_response=response)

    async def get_snapshot_progress(self, snapshot_id: str) -> dict[str, Any]:
        response = await self._request("GET", f"/datasets/v3/progress/{snapshot_id}")
        if not isinstance(response, dict):
            raise BrightDataClientError("Bright Data progress response was not an object")
        return response

    async def download_snapshot(
        self,
        snapshot_id: str,
        *,
        format_: str = "json",
    ) -> list[dict[str, Any]]:
        response = await self._request(
            "GET",
            f"/datasets/v3/snapshot/{snapshot_id}",
            params={"format": format_},
        )
        return _records_from_response(response)

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        if self._http_client is None:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
                headers=self._headers(),
            ) as client:
                return await self._request_with_retries(client, method, path, **kwargs)
        return await self._request_with_retries(self._http_client, method, path, **kwargs)

    async def _request_with_retries(
        self,
        client: httpx.AsyncClient,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> Any:
        headers = kwargs.pop("headers", {})
        merged_headers = {**self._headers(), **headers}
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = await client.request(method, path, headers=merged_headers, **kwargs)
                response.raise_for_status()
                if not response.content:
                    return {}
                content_type = response.headers.get("content-type", "")
                if "jsonl" in content_type or "ndjson" in content_type:
                    return _parse_json_lines(response.text)
                if "json" in content_type:
                    return response.json()
                try:
                    return response.json()
                except ValueError:
                    return {"body": response.text}
            except (httpx.HTTPStatusError, httpx.TransportError) as exc:
                last_error = exc
                if attempt >= self.max_retries or not _is_transient(exc):
                    break
                await asyncio.sleep(0.25 * (2**attempt))
        raise BrightDataClientError(f"Bright Data request failed: {last_error}") from last_error

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _is_transient(exc: Exception) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in {408, 425, 429, 500, 502, 503, 504}
    return False


def _first_string(data: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = data.get(key)
        if value is not None:
            return str(value)
    return None


def _extract_unlocker_body(response: Any) -> str:
    if isinstance(response, dict):
        body = response.get("body")
        if isinstance(body, str):
            return body
        if body is not None:
            return str(body)
    return str(response)


def _parse_json_lines(text: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            decoded = json.loads(line)
        except ValueError:
            continue
        if isinstance(decoded, dict):
            records.append(decoded)
    return records


def _records_from_response(response: Any) -> list[dict[str, Any]]:
    if isinstance(response, list):
        return [item for item in response if isinstance(item, dict)]
    if isinstance(response, dict):
        for key in ("records", "data", "result", "results"):
            value = response.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return [response]
    return []
