from __future__ import annotations

from typing import Protocol

from dennis_bot.monitors.models import (
    MonitorChange,
    MonitorDefinition,
    MonitorRunRecord,
    MonitorState,
    SocialActivityItemRecord,
)


class MonitorRepository(Protocol):
    async def get_monitor_state(self, monitor_name: str) -> MonitorState | None: ...

    async def upsert_monitor_state(self, state: MonitorState) -> None: ...

    async def has_seen_change_hash(self, monitor_name: str, content_hash: str) -> bool: ...

    async def record_change_hash(self, monitor_name: str, content_hash: str) -> None: ...

    async def create_monitor_run(self, run: MonitorRunRecord) -> MonitorRunRecord: ...

    async def create_social_activity_item(
        self, item: SocialActivityItemRecord
    ) -> SocialActivityItemRecord: ...

    async def set_monitor_enabled(self, monitor_name: str, enabled: bool) -> None: ...

    async def is_monitor_enabled(self, monitor_name: str) -> bool | None: ...


class KnowledgeUpdateService(Protocol):
    async def handle_monitor_change(self, change: MonitorChange) -> None: ...


class NotificationService(Protocol):
    async def send_monitor_notification(
        self,
        *,
        chat_id: int | None,
        change: MonitorChange,
        message: str,
    ) -> None: ...
