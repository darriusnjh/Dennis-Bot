from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal


MonitorType = Literal["website", "instagram_profile", "instagram_posts", "rss", "custom"]
Provider = Literal["direct", "brightdata"]
ActivityType = Literal[
    "profile_update",
    "post",
    "reel",
    "carousel",
    "comment",
    "tagged_media",
    "mention",
    "story",
    "highlight",
    "metric_snapshot",
    "unknown",
]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class MonitorDefinition:
    name: str
    url: str
    monitor_type: MonitorType
    provider: Provider = "brightdata"
    schedule: str = "interval:hours=6"
    change_detection_strategy: str = "normalized_content_hash"
    relevance_filter: str = "any_normalized_content_change"
    impact_policy: str = "notify_and_dispatch_kb_for_site_changes"
    notify_on_any_change: bool = True
    knowledge_update_enabled: bool = False
    target_chat_id: int | None = None
    source_handle: str | None = None
    provider_dataset_id: str | None = None
    enabled: bool = True


@dataclass(slots=True)
class MonitorState:
    monitor_name: str
    last_seen_external_id: str | None = None
    last_seen_permalink: str | None = None
    last_seen_hash: str | None = None
    last_seen_content: str | None = None
    last_seen_published_at: datetime | None = None
    last_checked_at: datetime | None = None
    last_notified_at: datetime | None = None


@dataclass(slots=True)
class NormalizedRecord:
    monitor_name: str
    source_type: Literal["official_site", "instagram"]
    url: str
    title: str
    summary: str
    content: str
    content_hash: str
    external_id: str | None = None
    permalink: str | None = None
    published_at: datetime | None = None
    activity_type: ActivityType | None = None
    media_type: str | None = None
    actor_handle: str | None = None
    caption: str | None = None
    engagement_snapshot: dict[str, Any] = field(default_factory=dict)
    raw_provider_record: dict[str, Any] = field(default_factory=dict)
    notify: bool = True


@dataclass(slots=True)
class MonitorChange:
    monitor: MonitorDefinition
    record: NormalizedRecord
    detected_at: datetime = field(default_factory=utc_now)
    reason: str = "normalized content hash changed"


@dataclass(slots=True)
class MonitorRunRecord:
    web_monitor_id: str
    provider: str
    provider_endpoint: str
    request_mode: str
    status: str
    started_at: datetime
    completed_at: datetime | None = None
    provider_request_id: str | None = None
    provider_snapshot_id: str | None = None
    records_returned: int = 0
    records_changed: int = 0
    error_code: str | None = None
    error_message: str | None = None
    id: str | None = None


@dataclass(slots=True)
class SocialActivityItemRecord:
    web_monitor_id: str
    platform: str
    external_id: str
    activity_type: ActivityType
    actor_handle: str | None
    permalink: str | None
    media_type: str
    caption: str | None
    caption_hash: str | None
    published_at: datetime | None
    detected_at: datetime
    thumbnail_ref: str | None = None
    engagement_snapshot: dict[str, Any] = field(default_factory=dict)
    raw_provider_record_ref: dict[str, Any] = field(default_factory=dict)
    notified_at: datetime | None = None
    id: str | None = None
