from __future__ import annotations

import pytest

from dennis_bot.monitors.models import MonitorDefinition
from dennis_bot.scheduler.service import MonitorScheduler, parse_schedule


class FakeMonitorService:
    def __init__(self, *, brightdata_configured: bool = True) -> None:
        self.brightdata_configured = brightdata_configured
        self.monitors = {
            "dennis_official_site": MonitorDefinition(
                name="dennis_official_site",
                url="https://www.dennistohsg.com/",
                monitor_type="website",
            )
        }
        self.ran: list[str] = []

    async def run_manual(self, monitor_name: str) -> list[str]:
        self.ran.append(monitor_name)
        return [monitor_name]

    async def run_monitor(self, monitor_name: str) -> list[str]:
        self.ran.append(monitor_name)
        return [monitor_name]

    async def active_monitors(self) -> list[MonitorDefinition]:
        return [monitor for monitor in self.monitors.values() if monitor.enabled]


@pytest.mark.asyncio
async def test_scheduler_manual_run_delegates_to_monitor_service() -> None:
    service = FakeMonitorService()
    scheduler = MonitorScheduler(monitor_service=service)  # type: ignore[arg-type]

    result = await scheduler.run_manual("dennis_official_site")

    assert result == ["dennis_official_site"]
    assert service.ran == ["dennis_official_site"]


def test_parse_interval_schedule() -> None:
    trigger = parse_schedule("interval:minutes=60")

    assert trigger.interval.total_seconds() == 3600


def test_parse_cron_schedule() -> None:
    trigger = parse_schedule("cron:hour=9,minute=30")

    assert str(trigger.fields[5]) == "9"
    assert str(trigger.fields[6]) == "30"


def test_scheduler_omits_jobs_when_brightdata_is_unconfigured() -> None:
    service = FakeMonitorService(brightdata_configured=False)
    scheduler = MonitorScheduler(monitor_service=service)  # type: ignore[arg-type]

    scheduler.add_default_jobs()

    assert scheduler.scheduler.get_jobs() == []


@pytest.mark.asyncio
async def test_scheduler_active_jobs_uses_monitor_service_enabled_view() -> None:
    service = FakeMonitorService()
    service.monitors["dennis_official_site"].enabled = False
    scheduler = MonitorScheduler(monitor_service=service)  # type: ignore[arg-type]

    await scheduler.add_active_jobs()

    assert scheduler.scheduler.get_jobs() == []
