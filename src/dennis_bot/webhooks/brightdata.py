from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request, status

from dennis_bot.brightdata.models import BrightDataRunMetadata, BrightDataSnapshot
from dennis_bot.monitors.models import MonitorChange
from dennis_bot.monitors.service import MonitorService


class BrightDataWebhookError(RuntimeError):
    """Raised when a Bright Data webhook cannot be trusted or normalized."""


@dataclass(frozen=True, slots=True)
class BrightDataWebhookPayload:
    monitor_name: str
    snapshot: BrightDataSnapshot


def validate_brightdata_webhook_secret(expected_secret: str, supplied_secret: str | None) -> None:
    if not expected_secret:
        raise BrightDataWebhookError("BRIGHTDATA_WEBHOOK_SECRET is not configured")
    if not supplied_secret or not secrets.compare_digest(expected_secret, supplied_secret):
        raise BrightDataWebhookError("Invalid Bright Data webhook secret")


def normalize_brightdata_webhook_payload(payload: dict[str, Any]) -> BrightDataWebhookPayload:
    monitor_name = _find_monitor_name(payload)
    records = _extract_records(payload)
    now = datetime.now(timezone.utc)
    metadata = BrightDataRunMetadata(
        endpoint=str(payload.get("endpoint") or payload.get("dataset_id") or "brightdata_webhook"),
        request_mode="webhook",
        status=_normalize_status(payload.get("status")),
        started_at=_parse_datetime(payload.get("started_at")) or now,
        completed_at=_parse_datetime(payload.get("completed_at")) or now,
        provider_request_id=_first_string(payload, "request_id", "provider_request_id", "id"),
        provider_snapshot_id=_first_string(payload, "snapshot_id", "snapshot", "provider_snapshot_id"),
        response_record_count=len(records),
        cost_record_count=_optional_int(payload.get("cost_record_count") or payload.get("record_count")),
        error_message=_first_string(payload, "error", "error_message"),
    )
    return BrightDataWebhookPayload(
        monitor_name=monitor_name,
        snapshot=BrightDataSnapshot(records=records, metadata=metadata, raw_response=payload),
    )


async def process_brightdata_webhook(
    *,
    payload: dict[str, Any],
    supplied_secret: str | None,
    expected_secret: str,
    monitor_service: MonitorService,
) -> list[MonitorChange]:
    validate_brightdata_webhook_secret(expected_secret, supplied_secret)
    normalized = normalize_brightdata_webhook_payload(payload)
    return await monitor_service.process_snapshot(normalized.monitor_name, normalized.snapshot)


def create_brightdata_webhook_router(
    *,
    monitor_service: MonitorService,
    webhook_secret: str,
) -> APIRouter:
    router = APIRouter()

    @router.post("/webhooks/brightdata")
    async def brightdata_webhook(
        request: Request,
        x_brightdata_webhook_secret: str | None = Header(default=None),
        x_webhook_secret: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        supplied_secret = _secret_from_headers(
            x_brightdata_webhook_secret=x_brightdata_webhook_secret,
            x_webhook_secret=x_webhook_secret,
            authorization=authorization,
        )
        try:
            payload = await request.json()
            if not isinstance(payload, dict):
                raise BrightDataWebhookError("Bright Data webhook payload must be a JSON object")
            changes = await process_brightdata_webhook(
                payload=payload,
                supplied_secret=supplied_secret,
                expected_secret=webhook_secret,
                monitor_service=monitor_service,
            )
        except BrightDataWebhookError as exc:
            detail = str(exc)
            if "not configured" in detail:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=detail,
                ) from exc
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail) from exc
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        return {"ok": True, "changes": len(changes)}

    return router


def _secret_from_headers(
    *,
    x_brightdata_webhook_secret: str | None,
    x_webhook_secret: str | None,
    authorization: str | None,
) -> str | None:
    if x_brightdata_webhook_secret:
        return x_brightdata_webhook_secret
    if x_webhook_secret:
        return x_webhook_secret
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def _find_monitor_name(payload: dict[str, Any]) -> str:
    candidates = [
        _first_string(payload, "monitor_name", "web_monitor_id", "monitor"),
        _first_string(_object(payload.get("metadata")), "monitor_name", "web_monitor_id", "monitor"),
        _first_string(_object(payload.get("custom")), "monitor_name", "web_monitor_id", "monitor"),
    ]
    for candidate in candidates:
        if candidate:
            return candidate
    raise BrightDataWebhookError("Bright Data webhook payload did not include monitor_name")


def _extract_records(payload: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("records", "data", "results", "items"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested = _extract_records(value)
            return nested or [value]
    if isinstance(payload.get("record"), dict):
        return [payload["record"]]
    return [payload]


def _normalize_status(value: Any) -> str:
    status_value = str(value or "succeeded").lower()
    if status_value in {"ready", "done", "completed", "success"}:
        return "succeeded"
    if status_value in {"failed", "error", "canceled", "cancelled"}:
        return "failed"
    return status_value


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value)
    if text.isdigit():
        return datetime.fromtimestamp(int(text), tz=timezone.utc)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _first_string(data: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = data.get(key)
        if value is not None and value != "":
            return str(value)
    return None


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
