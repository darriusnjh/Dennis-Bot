from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from contextlib import suppress
from typing import Any

import aiosqlite
from fastapi import FastAPI

from dennis_bot.admin.policy import AdminPolicy
from dennis_bot.agents.knowledge_update.agent import KnowledgeUpdateAgent
from dennis_bot.brightdata.client import BrightDataClient
from dennis_bot.config import Settings, get_settings
from dennis_bot.db.migrations import run_migrations
from dennis_bot.knowledge.service import KnowledgeService
from dennis_bot.llm.client import OpenAIChatClient
from dennis_bot.logging_config import configure_logging
from dennis_bot.memory.service import MemoryService
from dennis_bot.monitors.default_monitors import default_dennis_monitors
from dennis_bot.monitors.health import brightdata_config_health, monitor_count_health, scheduler_health
from dennis_bot.monitors.service import MonitorService
from dennis_bot.orchestrator.service import ConversationOrchestrator
from dennis_bot.repositories.core import ChatRepository, KnowledgeRepository, MemoryRepository
from dennis_bot.runtime.adapters import (
    KnowledgeCommandAdapter,
    KnowledgeUpdateMonitorAdapter,
    MemoryCommandAdapter,
    NaturalMessageHandler,
    SQLiteMemorySessionRepository,
    SQLiteMonitorRepository,
    SQLiteStickerAliasStore,
    TelegramMetadataRecorder,
    TelegramNotificationService,
    WatchCommandAdapter,
)
from dennis_bot.scheduler.service import MonitorScheduler
from dennis_bot.stickers.service import StickerService
from dennis_bot.stickers.store import InMemoryStickerAliasStore
from dennis_bot.telegram.client import TelegramClient
from dennis_bot.telegram.models import NormalizedTelegramUpdate
from dennis_bot.telegram.polling import TelegramPollingRunner
from dennis_bot.telegram.router import CommandRouter
from dennis_bot.telegram.webhook import build_telegram_webhook_router
from dennis_bot.tools.instagram_activity import (
    InstagramActivityTool,
    RuntimeToolPlanner,
    SQLiteInstagramActivityRepository,
)
from dennis_bot.webhooks.brightdata import create_brightdata_webhook_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = app.state.settings
    mode = "polling" if settings.telegram_use_polling else "webhook"
    errors = settings.validate_for_runtime(mode=mode)
    if errors:
        logger.warning("Runtime configuration has %d issue(s): %s", len(errors), "; ".join(errors))
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    if settings.database_path.exists() and settings.database_path.is_dir():
        raise RuntimeError(
            "DATABASE_PATH must point to a SQLite file, not a directory. "
            f"Got {settings.database_path!s}; use a path like /app/data/dennis_bot.sqlite3."
        )
    db_connection = await aiosqlite.connect(settings.database_path)
    db_connection.row_factory = aiosqlite.Row
    await db_connection.execute("PRAGMA foreign_keys = ON")
    await db_connection.execute("PRAGMA journal_mode = WAL")
    await run_migrations(db_connection)
    await db_connection.commit()
    app.state.db_connection = db_connection
    await _wire_runtime_services(app, db_connection)
    await _start_telegram_ingress(app)
    yield
    polling_task = getattr(app.state, "telegram_polling_task", None)
    if polling_task is not None:
        polling_task.cancel()
        with suppress(asyncio.CancelledError):
            await polling_task
    scheduler = getattr(app.state, "monitor_scheduler", None)
    if scheduler is not None:
        await scheduler.shutdown()
    memory_service = getattr(app.state, "memory_service", None)
    if memory_service is not None:
        try:
            await memory_service.finalize_all_active_sessions(reason="shutdown")
        except Exception:
            logger.exception("Failed to finalize active memory sessions during shutdown")
    telegram_client = getattr(app.state, "telegram_client", None)
    if telegram_client is not None:
        await telegram_client.aclose()
    await db_connection.close()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    app = FastAPI(title="Dennis Bot", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    telegram_client = TelegramClient(settings.telegram_bot_token)
    sticker_service = StickerService(telegram_client, InMemoryStickerAliasStore())
    policy = AdminPolicy.from_settings(settings)
    command_router = CommandRouter(
        telegram=telegram_client,
        policy=policy,
        settings=settings,
        sticker_service=sticker_service,
    )
    app.state.telegram_client = telegram_client
    app.state.command_router = command_router
    app.state.admin_policy = policy
    app.include_router(
        build_telegram_webhook_router(
            settings,
            command_router,
            bot_username=settings.telegram_bot_username,
            bot_user_id=settings.telegram_bot_user_id,
        )
    )

    @app.get("/health")
    async def health() -> dict[str, Any]:
        mode = "polling" if settings.telegram_use_polling else "webhook"
        config_errors = settings.validate_for_runtime(mode=mode)
        subsystems: list[dict[str, Any]] = [
            {
                "ok": bool(settings.simplemem_mcp_url and settings.simplemem_mcp_token),
                "subsystem": "simplemem",
                "tenant_id": settings.simplemem_tenant_id,
                "project": settings.simplemem_project,
            },
            brightdata_config_health(settings),
        ]
        if mode == "webhook":
            webhook_error = getattr(app.state, "telegram_webhook_registration_error", None)
            telegram_config_errors = [
                error
                for error in config_errors
                if error.startswith("TELEGRAM") or error.startswith("BASE_URL")
            ]
            subsystems.append(
                {
                    "ok": webhook_error is None and not telegram_config_errors,
                    "subsystem": "telegram_webhook",
                    "url": settings.telegram_webhook_url,
                    "error": webhook_error,
                    "config_errors": telegram_config_errors,
                }
            )
        monitor_service = getattr(app.state, "monitor_service", None)
        if monitor_service is not None:
            subsystems.append(await monitor_count_health(monitor_service))
        scheduler = getattr(app.state, "monitor_scheduler", None)
        if scheduler is not None:
            subsystems.append(scheduler_health(scheduler))
        return {
            "ok": not config_errors,
            "mode": mode,
            "config_errors": config_errors,
            "simplemem_project": settings.simplemem_project,
            "simplemem_tenant_id": settings.simplemem_tenant_id,
            "subsystems": subsystems,
        }

    return app


async def _wire_runtime_services(app: FastAPI, connection: aiosqlite.Connection) -> None:
    settings: Settings = app.state.settings
    telegram_client: TelegramClient = app.state.telegram_client
    command_router: CommandRouter = app.state.command_router

    app.state.telegram_recorder = TelegramMetadataRecorder(
        connection,
        trusted_group_chat_id=settings.trusted_group_chat_id,
    )

    memory_service = None
    if settings.simplemem_mcp_url and settings.simplemem_mcp_token:
        memory_service = MemoryService.from_settings(
            settings,
            SQLiteMemorySessionRepository(connection),
            memory_repository=MemoryRepository(connection),
        )
    app.state.memory_service = memory_service
    command_router.memory_service = MemoryCommandAdapter(memory_service)

    knowledge_repository = KnowledgeRepository(connection)
    chat_repository = ChatRepository(connection)
    knowledge_service = KnowledgeService(
        repository=knowledge_repository,
        chat_repository=chat_repository,
    )
    await knowledge_service.ensure_default_state()
    await connection.commit()
    knowledge_agent = KnowledgeUpdateAgent(
        knowledge_service=knowledge_service,
        repository=knowledge_repository,
    )
    command_router.knowledge_service = KnowledgeCommandAdapter(knowledge_service, knowledge_agent)

    sticker_service = StickerService(telegram_client, SQLiteStickerAliasStore(connection))
    command_router.sticker_service = sticker_service
    if settings.telegram_sticker_packs:
        try:
            await sticker_service.sync_packs(settings.telegram_sticker_packs)
        except Exception:
            logger.exception("Failed to sync configured sticker packs")

    orchestrator = None
    if settings.llm_api_key and memory_service is not None:
        llm_client = OpenAIChatClient(
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            base_url=settings.llm_base_url,
        )
        orchestrator = ConversationOrchestrator(
            llm_client=llm_client,
            memory_service=memory_service,
            knowledge_service=knowledge_service,
            runtime_tool_planner=RuntimeToolPlanner(),
            instagram_activity_tool=InstagramActivityTool(
                SQLiteInstagramActivityRepository(connection)
            ),
        )
    app.state.message_handler = NaturalMessageHandler(
        telegram=telegram_client,
        orchestrator=orchestrator,
        policy=command_router.policy,
        knowledge_service=knowledge_service,
        sticker_service=sticker_service,
    )

    monitor_repository = SQLiteMonitorRepository(connection)
    monitors = default_dennis_monitors(settings)
    for monitor in monitors:
        await monitor_repository.ensure_monitor_definition(monitor)
    monitor_service = MonitorService(
        brightdata_client=BrightDataClient(
            api_key=settings.brightdata_api_key,
            web_unlocker_zone=settings.brightdata_web_unlocker_zone,
        ),
        repository=monitor_repository,
        monitors=monitors,
        notification_service=TelegramNotificationService(telegram_client),
        knowledge_update_service=KnowledgeUpdateMonitorAdapter(knowledge_agent),
    )
    app.state.monitor_service = monitor_service
    if not getattr(app.state, "brightdata_router_included", False):
        app.include_router(
            create_brightdata_webhook_router(
                monitor_service=monitor_service,
                webhook_secret=settings.brightdata_webhook_secret,
            )
        )
        app.state.brightdata_router_included = True
    command_router.watch_service = WatchCommandAdapter(monitor_service)

    monitor_scheduler = MonitorScheduler(monitor_service=monitor_service)
    await monitor_scheduler.add_active_jobs()
    monitor_scheduler.start()
    app.state.monitor_scheduler = monitor_scheduler


async def _start_telegram_ingress(app: FastAPI) -> None:
    settings: Settings = app.state.settings
    telegram_client: TelegramClient = app.state.telegram_client
    config_errors = settings.validate_for_runtime(
        mode="polling" if settings.telegram_use_polling else "webhook"
    )
    if config_errors:
        return

    if settings.telegram_use_polling:
        await telegram_client.delete_webhook(drop_pending_updates=False)
        runner = TelegramPollingRunner(
            telegram_client,
            lambda update: _handle_normalized_update(app, update),
            bot_username=settings.telegram_bot_username,
            bot_user_id=settings.telegram_bot_user_id,
            allowed_updates=["message", "edited_message", "channel_post", "edited_channel_post"],
        )
        app.state.telegram_polling_runner = runner
        app.state.telegram_polling_task = asyncio.create_task(runner.run_forever())
        return

    if settings.app_env == "production":
        try:
            await telegram_client.set_webhook(
                settings.telegram_webhook_url,
                secret_token=settings.telegram_webhook_secret,
                allowed_updates=[
                    "message",
                    "edited_message",
                    "channel_post",
                    "edited_channel_post",
                ],
            )
            app.state.telegram_webhook_registration_error = None
        except Exception as exc:
            app.state.telegram_webhook_registration_error = str(exc)
            logger.exception("Failed to register Telegram webhook; app will keep running")


async def _handle_normalized_update(app: FastAPI, update: NormalizedTelegramUpdate) -> bool:
    recorder = getattr(app.state, "telegram_recorder", None)
    if recorder is not None:
        await recorder.record_metadata(update)
        if not await recorder.claim_update(update):
            logger.info("Skipping duplicate Telegram update_id=%s", update.update_id)
            return True
    command_router: CommandRouter = app.state.command_router
    handled = await command_router.handle_update(update)
    if not handled:
        message_handler = getattr(app.state, "message_handler", None)
        if message_handler is not None:
            handled = await message_handler.handle_update(update)
    return handled
