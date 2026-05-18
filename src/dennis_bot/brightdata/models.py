from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal


BrightDataRequestMode = Literal["sync", "async", "webhook"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class BrightDataRunMetadata:
    endpoint: str
    request_mode: BrightDataRequestMode
    status: str
    started_at: datetime = field(default_factory=utc_now)
    completed_at: datetime | None = None
    provider_request_id: str | None = None
    provider_snapshot_id: str | None = None
    response_record_count: int = 0
    cost_record_count: int | None = None
    error_message: str | None = None


@dataclass(slots=True)
class BrightDataSnapshot:
    records: list[dict[str, Any]]
    metadata: BrightDataRunMetadata
    raw_response: Any | None = None

