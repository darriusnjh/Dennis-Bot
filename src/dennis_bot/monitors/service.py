from __future__ import annotations

import logging
from datetime import datetime, timezone

from dennis_bot.brightdata.client import BrightDataClient, BrightDataClientError
from dennis_bot.brightdata.models import BrightDataSnapshot
from dennis_bot.monitors.models import (
    MonitorChange,
    MonitorDefinition,
    MonitorRunRecord,
    MonitorState,
    NormalizedRecord,
    SocialActivityItemRecord,
)
from dennis_bot.monitors.normalization import (
    normalize_instagram_response,
    normalize_official_site_response,
    stable_hash,
)
from dennis_bot.monitors.protocols import (
    KnowledgeUpdateService,
    MonitorRepository,
    NotificationService,
)

logger = logging.getLogger(__name__)

INSTAGRAM_POST_POLL_LIMIT = 1


class MonitorService:
    def __init__(
        self,
        *,
        brightdata_client: BrightDataClient,
        repository: MonitorRepository,
        monitors: list[MonitorDefinition],
        notification_service: NotificationService | None = None,
        knowledge_update_service: KnowledgeUpdateService | None = None,
    ) -> None:
        self.brightdata_client = brightdata_client
        self.repository = repository
        self.monitors = {monitor.name: monitor for monitor in monitors}
        self.notification_service = notification_service
        self.knowledge_update_service = knowledge_update_service

    @property
    def brightdata_configured(self) -> bool:
        return self.brightdata_client.is_configured

    async def active_monitors(self) -> list[MonitorDefinition]:
        active: list[MonitorDefinition] = []
        for monitor in self.monitors.values():
            if await self.is_monitor_enabled(monitor.name):
                active.append(monitor)
        return active

    async def is_monitor_enabled(self, monitor_name: str) -> bool:
        monitor = self.monitors.get(monitor_name)
        if monitor is None:
            raise KeyError(f"Unknown monitor: {monitor_name}")
        get_enabled = getattr(self.repository, "is_monitor_enabled", None)
        persisted = await get_enabled(monitor_name) if get_enabled is not None else None
        return monitor.enabled if persisted is None else persisted

    async def pause_monitor(self, monitor_name: str) -> None:
        if monitor_name not in self.monitors:
            raise KeyError(f"Unknown monitor: {monitor_name}")
        set_enabled = getattr(self.repository, "set_monitor_enabled", None)
        if set_enabled is not None:
            await set_enabled(monitor_name, False)
        self.monitors[monitor_name].enabled = False

    async def resume_monitor(self, monitor_name: str) -> None:
        if monitor_name not in self.monitors:
            raise KeyError(f"Unknown monitor: {monitor_name}")
        set_enabled = getattr(self.repository, "set_monitor_enabled", None)
        if set_enabled is not None:
            await set_enabled(monitor_name, True)
        self.monitors[monitor_name].enabled = True

    async def run_manual(self, monitor_name: str) -> list[MonitorChange]:
        return await self.run_monitor(monitor_name)

    async def run_monitor(self, monitor_name: str) -> list[MonitorChange]:
        monitor = self.monitors.get(monitor_name)
        if monitor is None:
            raise KeyError(f"Unknown monitor: {monitor_name}")
        if not await self.is_monitor_enabled(monitor_name):
            return []
        if not self.brightdata_client.is_configured:
            await self._record_skipped_run(monitor, "BRIGHTDATA_API_KEY is required")
            return []

        try:
            snapshot = await self._fetch_snapshot(monitor)
            return await self.process_snapshot(monitor_name, snapshot)
        except Exception as exc:
            logger.exception("Monitor %s failed", monitor.name)
            await self._record_failed_run(monitor, exc)
            raise

    async def process_snapshot(
        self,
        monitor_name: str,
        snapshot: BrightDataSnapshot,
    ) -> list[MonitorChange]:
        monitor = self.monitors.get(monitor_name)
        if monitor is None:
            raise KeyError(f"Unknown monitor: {monitor_name}")
        records = self._normalize(monitor, snapshot)
        changes = await self._process_normalized_records(monitor, records)
        await self._record_run(
            monitor,
            snapshot,
            records_changed=len(changes),
            status=snapshot.metadata.status or "succeeded",
        )
        return changes

    async def process_records(
        self,
        monitor_name: str,
        records: list[NormalizedRecord],
        *,
        snapshot: BrightDataSnapshot | None = None,
    ) -> list[MonitorChange]:
        monitor = self.monitors.get(monitor_name)
        if monitor is None:
            raise KeyError(f"Unknown monitor: {monitor_name}")
        changes = await self._process_normalized_records(monitor, records)
        if snapshot is not None:
            await self._record_run(
                monitor,
                snapshot,
                records_changed=len(changes),
                status=snapshot.metadata.status or "succeeded",
            )
        return changes

    async def _process_normalized_records(
        self,
        monitor: MonitorDefinition,
        records: list[NormalizedRecord],
    ) -> list[MonitorChange]:
        changes = await self._detect_changes(monitor, records)
        if not changes:
            await self._mark_checked(monitor, records)
        for change in changes:
            await self._persist_change(change)
            await self._dispatch_change(change)
        return changes

    async def _fetch_snapshot(self, monitor: MonitorDefinition) -> BrightDataSnapshot:
        if monitor.monitor_type == "website":
            return await self.brightdata_client.fetch_web_unlocker(monitor.url)
        if monitor.monitor_type.startswith("instagram"):
            if monitor.provider_dataset_id:
                if monitor.monitor_type == "instagram_posts":
                    return await self.brightdata_client.scrape_dataset_discover_by_url_sync(
                        dataset_id=monitor.provider_dataset_id,
                        inputs=[
                            {
                                "url": monitor.url,
                                "num_of_posts": INSTAGRAM_POST_POLL_LIMIT,
                                "post_type": "Post",
                            }
                        ],
                    )
                return await self.brightdata_client.request_dataset_sync(
                    dataset_id=monitor.provider_dataset_id,
                    inputs=[{"url": monitor.url}],
                )
            return await self.brightdata_client.fetch_web_unlocker(monitor.url)
        raise BrightDataClientError(f"Unsupported monitor type: {monitor.monitor_type}")

    def _normalize(
        self,
        monitor: MonitorDefinition,
        snapshot: BrightDataSnapshot,
    ) -> list[NormalizedRecord]:
        if monitor.monitor_type == "website":
            return [normalize_official_site_response(monitor, snapshot.records[0])]
        if monitor.monitor_type.startswith("instagram"):
            records = normalize_instagram_response(monitor, snapshot.records)
            if monitor.monitor_type == "instagram_posts":
                return _most_recent_records(records, limit=INSTAGRAM_POST_POLL_LIMIT)
            return records
        return []

    async def _detect_changes(
        self,
        monitor: MonitorDefinition,
        records: list[NormalizedRecord],
    ) -> list[MonitorChange]:
        changes: list[MonitorChange] = []
        for record in records:
            if await self.repository.has_seen_change_hash(monitor.name, record.content_hash):
                continue
            state = await self.repository.get_monitor_state(monitor.name)
            if state and state.last_seen_hash == record.content_hash:
                await self.repository.record_change_hash(monitor.name, record.content_hash)
                continue
            changes.append(MonitorChange(monitor=monitor, record=record))
        return changes

    async def _mark_checked(
        self,
        monitor: MonitorDefinition,
        records: list[NormalizedRecord],
    ) -> None:
        now = datetime.now(timezone.utc)
        latest = records[0] if records else None
        state = await self.repository.get_monitor_state(monitor.name)
        previous = state or MonitorState(monitor_name=monitor.name)
        await self.repository.upsert_monitor_state(
            MonitorState(
                monitor_name=monitor.name,
                last_seen_external_id=latest.external_id
                if latest
                else previous.last_seen_external_id,
                last_seen_permalink=latest.permalink if latest else previous.last_seen_permalink,
                last_seen_hash=latest.content_hash if latest else previous.last_seen_hash,
                last_seen_content=latest.content if latest else previous.last_seen_content,
                last_seen_published_at=latest.published_at
                if latest
                else previous.last_seen_published_at,
                last_checked_at=now,
                last_notified_at=previous.last_notified_at,
            )
        )

    async def _persist_change(self, change: MonitorChange) -> None:
        record = change.record
        now = change.detected_at
        await self.repository.record_change_hash(record.monitor_name, record.content_hash)
        await self.repository.upsert_monitor_state(
            MonitorState(
                monitor_name=record.monitor_name,
                last_seen_external_id=record.external_id,
                last_seen_permalink=record.permalink,
                last_seen_hash=record.content_hash,
                last_seen_content=record.content,
                last_seen_published_at=record.published_at,
                last_checked_at=now,
                last_notified_at=now if record.notify else None,
            )
        )
        if record.source_type == "instagram":
            external_id = record.external_id or stable_hash(record.permalink or record.content)
            await self.repository.create_social_activity_item(
                SocialActivityItemRecord(
                    web_monitor_id=record.monitor_name,
                    platform="instagram",
                    external_id=external_id,
                    activity_type=record.activity_type or "unknown",
                    actor_handle=record.actor_handle,
                    permalink=record.permalink,
                    media_type=record.media_type or "unknown",
                    caption=record.caption,
                    caption_hash=stable_hash(record.caption) if record.caption else None,
                    published_at=record.published_at,
                    detected_at=now,
                    engagement_snapshot=record.engagement_snapshot,
                    raw_provider_record_ref=record.raw_provider_record,
                    notified_at=now if record.notify else None,
                )
            )

    async def _dispatch_change(self, change: MonitorChange) -> None:
        should_dispatch_kb = change.monitor.knowledge_update_enabled
        if should_dispatch_kb and self.knowledge_update_service is not None:
            await self.knowledge_update_service.handle_monitor_change(change)
        if change.record.notify and self.notification_service is not None:
            await self.notification_service.send_monitor_notification(
                chat_id=change.monitor.target_chat_id,
                change=change,
                message=format_monitor_notification(change),
            )

    async def _record_run(
        self,
        monitor: MonitorDefinition,
        snapshot: BrightDataSnapshot,
        *,
        records_changed: int,
        status: str,
    ) -> None:
        metadata = snapshot.metadata
        await self.repository.create_monitor_run(
            MonitorRunRecord(
                web_monitor_id=monitor.name,
                provider=monitor.provider,
                provider_endpoint=metadata.endpoint,
                provider_request_id=metadata.provider_request_id,
                provider_snapshot_id=metadata.provider_snapshot_id,
                request_mode=metadata.request_mode,
                status=status,
                records_returned=metadata.response_record_count,
                records_changed=records_changed,
                started_at=metadata.started_at,
                completed_at=metadata.completed_at,
                error_message=metadata.error_message,
            )
        )

    async def _record_skipped_run(self, monitor: MonitorDefinition, message: str) -> None:
        now = datetime.now(timezone.utc)
        await self.repository.create_monitor_run(
            MonitorRunRecord(
                web_monitor_id=monitor.name,
                provider=monitor.provider,
                provider_endpoint="brightdata",
                request_mode="sync",
                status="skipped",
                started_at=now,
                completed_at=now,
                error_message=message,
            )
        )

    async def _record_failed_run(self, monitor: MonitorDefinition, exc: Exception) -> None:
        now = datetime.now(timezone.utc)
        await self.repository.create_monitor_run(
            MonitorRunRecord(
                web_monitor_id=monitor.name,
                provider=monitor.provider,
                provider_endpoint="brightdata",
                request_mode="sync",
                status="failed",
                started_at=now,
                completed_at=now,
                error_code=exc.__class__.__name__,
                error_message=str(exc),
            )
        )


def format_monitor_notification(change: MonitorChange) -> str:
    record = change.record
    if record.source_type == "instagram":
        return _format_instagram_notification(change)

    timestamp = change.detected_at.isoformat()
    lines = [
        f"{record.title}",
        f"URL: {record.permalink or record.url}",
        f"Detected: {timestamp}",
        f"Matched: {change.reason}",
    ]
    if record.source_type == "instagram":
        lines.insert(1, f"Activity: {record.activity_type or 'unknown'}")
        if record.media_type:
            lines.insert(2, f"Media: {record.media_type}")
        if record.published_at:
            lines.insert(3, f"Published: {record.published_at.isoformat()}")
    if record.summary:
        lines.append(f"Summary: {record.summary}")
    return "\n".join(lines)


def _most_recent_records(records: list[NormalizedRecord], *, limit: int) -> list[NormalizedRecord]:
    if limit <= 0:
        return []
    oldest = datetime.min.replace(tzinfo=timezone.utc)
    indexed = list(enumerate(records))
    indexed.sort(
        key=lambda item: (item[1].published_at or oldest, -item[0]),
        reverse=True,
    )
    return [record for _, record in indexed[:limit]]


def _format_instagram_notification(change: MonitorChange) -> str:
    record = change.record
    media_type = (record.media_type or record.activity_type or "post").replace("_", " ")
    tagline = _notification_tagline(record.summary or record.caption or record.title)
    link = record.permalink or record.url
    lines = [
        f"I just posted something new on Instagram - a new {media_type}.",
    ]
    if tagline:
        lines.append(tagline)
    if link:
        lines.append(f"Take a look: {link}")
    return "\n\n".join(lines)


def _notification_tagline(value: str, *, limit: int = 180) -> str:
    cleaned = " ".join(value.split())
    if not cleaned:
        return ""
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "..."
