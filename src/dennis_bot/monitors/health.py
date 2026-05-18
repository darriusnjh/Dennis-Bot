from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from dennis_bot.config import Settings


class DatabaseLike(Protocol):
    def connect(self) -> Any: ...


class SimpleMemLike(Protocol):
    async def health_check(self) -> dict[str, Any]: ...


class MonitorServiceLike(Protocol):
    monitors: dict[str, Any]

    async def active_monitors(self) -> list[Any]: ...


async def database_health(database: DatabaseLike) -> dict[str, Any]:
    try:
        async with database.connect() as connection:
            await connection.execute("SELECT 1")
        return {"ok": True, "subsystem": "database"}
    except Exception as exc:
        return {
            "ok": False,
            "subsystem": "database",
            "error": exc.__class__.__name__,
        }


async def simplemem_health(simplemem_client: SimpleMemLike | None) -> dict[str, Any]:
    if simplemem_client is None:
        return {"ok": False, "subsystem": "simplemem", "error": "not_configured"}
    try:
        result = await simplemem_client.health_check()
    except Exception as exc:
        return {"ok": False, "subsystem": "simplemem", "error": exc.__class__.__name__}
    return {"ok": bool(result.get("ok", True)), "subsystem": "simplemem", "details": result}


def brightdata_config_health(settings: Settings) -> dict[str, Any]:
    missing: list[str] = []
    if not settings.brightdata_api_key:
        missing.append("BRIGHTDATA_API_KEY")
    if not settings.brightdata_web_unlocker_zone:
        missing.append("BRIGHTDATA_WEB_UNLOCKER_ZONE")
    if not settings.brightdata_webhook_secret:
        missing.append("BRIGHTDATA_WEBHOOK_SECRET")
    has_instagram_dataset = any(
        [
            settings.brightdata_instagram_dataset_id_profile,
            settings.brightdata_instagram_dataset_id_posts,
            settings.brightdata_instagram_dataset_id_reels,
            settings.brightdata_instagram_dataset_id_comments,
        ]
    )
    return {
        "ok": not missing,
        "subsystem": "brightdata",
        "missing": missing,
        "instagram_dataset_configured": has_instagram_dataset,
    }


def scheduler_health(scheduler: Any | None) -> dict[str, Any]:
    if scheduler is None:
        return {"ok": False, "subsystem": "scheduler", "running": False}
    return {
        "ok": scheduler.scheduler.running,
        "subsystem": "scheduler",
        "running": scheduler.scheduler.running,
        "jobs": len(scheduler.scheduler.get_jobs()),
    }


async def monitor_count_health(monitor_service: MonitorServiceLike) -> dict[str, Any]:
    active = await monitor_service.active_monitors()
    return {
        "ok": True,
        "subsystem": "monitors",
        "configured": len(monitor_service.monitors),
        "active": len(active),
    }


async def collect_subsystem_health(
    checks: list[Callable[[], dict[str, Any] | Awaitable[dict[str, Any]]]],
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for check in checks:
        result = check()
        if hasattr(result, "__await__"):
            result = await result  # type: ignore[assignment]
        results.append(result)  # type: ignore[arg-type]
    return {"ok": all(item.get("ok") for item in results), "subsystems": results}
