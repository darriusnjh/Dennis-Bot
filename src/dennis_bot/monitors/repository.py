from __future__ import annotations

from dennis_bot.monitors.models import (
    MonitorRunRecord,
    MonitorState,
    SocialActivityItemRecord,
)


class InMemoryMonitorRepository:
    """Test and local-development repository implementing monitor repository protocols."""

    def __init__(self) -> None:
        self.states: dict[str, MonitorState] = {}
        self.hashes: set[tuple[str, str]] = set()
        self.runs: list[MonitorRunRecord] = []
        self.social_items: list[SocialActivityItemRecord] = []
        self.enabled_overrides: dict[str, bool] = {}

    async def get_monitor_state(self, monitor_name: str) -> MonitorState | None:
        return self.states.get(monitor_name)

    async def upsert_monitor_state(self, state: MonitorState) -> None:
        self.states[state.monitor_name] = state

    async def has_seen_change_hash(self, monitor_name: str, content_hash: str) -> bool:
        return (monitor_name, content_hash) in self.hashes

    async def record_change_hash(self, monitor_name: str, content_hash: str) -> None:
        self.hashes.add((monitor_name, content_hash))

    async def create_monitor_run(self, run: MonitorRunRecord) -> MonitorRunRecord:
        run.id = run.id or f"run_{len(self.runs) + 1}"
        self.runs.append(run)
        return run

    async def create_social_activity_item(
        self, item: SocialActivityItemRecord
    ) -> SocialActivityItemRecord:
        item.id = item.id or f"social_{len(self.social_items) + 1}"
        self.social_items.append(item)
        return item

    async def set_monitor_enabled(self, monitor_name: str, enabled: bool) -> None:
        self.enabled_overrides[monitor_name] = enabled

    async def is_monitor_enabled(self, monitor_name: str) -> bool | None:
        return self.enabled_overrides.get(monitor_name)
