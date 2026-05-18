from __future__ import annotations

import inspect
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from dennis_bot.monitors.models import MonitorDefinition
from dennis_bot.monitors.service import MonitorService


class MonitorScheduler:
    """Thin APScheduler wrapper for scheduled and manual monitor execution."""

    def __init__(
        self,
        *,
        monitor_service: MonitorService,
        scheduler: AsyncIOScheduler | None = None,
    ) -> None:
        self.monitor_service = monitor_service
        self.scheduler = scheduler or AsyncIOScheduler()

    def add_default_jobs(self) -> None:
        if not self.monitor_service.brightdata_configured:
            return
        for monitor in self.monitor_service.monitors.values():
            self.add_monitor_job(monitor)

    async def add_active_jobs(self) -> None:
        if not self.monitor_service.brightdata_configured:
            return
        for monitor in await self.monitor_service.active_monitors():
            self.add_monitor_job(monitor)

    def add_monitor_job(self, monitor: MonitorDefinition) -> None:
        if not self.monitor_service.brightdata_configured or not monitor.enabled:
            return
        self.scheduler.add_job(
            self.monitor_service.run_monitor,
            trigger=parse_schedule(monitor.schedule),
            args=[monitor.name],
            id=f"monitor:{monitor.name}",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )

    async def run_manual(self, monitor_name: str) -> Any:
        return await self.monitor_service.run_manual(monitor_name)

    def start(self) -> None:
        if not self.scheduler.running:
            self.scheduler.start()

    async def shutdown(self) -> None:
        result = self.scheduler.shutdown(wait=True)
        if inspect.isawaitable(result):
            await result


def parse_schedule(schedule: str) -> IntervalTrigger | CronTrigger:
    if schedule.startswith("interval:"):
        values = _parse_key_values(schedule.removeprefix("interval:"))
        return IntervalTrigger(**values)
    if schedule.startswith("cron:"):
        values = _parse_key_values(schedule.removeprefix("cron:"))
        return CronTrigger(**values)
    raise ValueError(f"Unsupported monitor schedule: {schedule}")


def _parse_key_values(value: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for part in value.split(","):
        if not part.strip():
            continue
        key, raw = part.split("=", 1)
        raw = raw.strip()
        result[key.strip()] = int(raw) if raw.isdigit() else raw
    return result
