from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, status

from dennis_bot.config import Settings
from dennis_bot.telegram.models import NormalizedTelegramUpdate
from dennis_bot.telegram.normalize import normalize_update
from dennis_bot.telegram.router import CommandRouter

logger = logging.getLogger(__name__)


def build_telegram_webhook_router(
    settings: Settings,
    command_router: CommandRouter,
    *,
    bot_username: str | None = None,
    bot_user_id: int | None = None,
) -> APIRouter:
    router = APIRouter()

    @router.post(settings.telegram_webhook_path)
    async def telegram_webhook(
        request: Request,
        background_tasks: BackgroundTasks,
        x_telegram_bot_api_secret_token: str | None = Header(default=None),
    ) -> dict[str, Any]:
        if (
            settings.telegram_webhook_secret
            and x_telegram_bot_api_secret_token != settings.telegram_webhook_secret
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid secret",
            )

        payload = await request.json()
        update = normalize_update(payload, bot_username=bot_username, bot_user_id=bot_user_id)
        if update is None:
            logger.info("Ignoring unsupported Telegram update")
            return {"ok": True, "handled": False}
        background_tasks.add_task(_handle_webhook_update, request.app, command_router, update)
        return {"ok": True, "accepted": True}

    return router


async def _handle_webhook_update(
    app: Any,
    command_router: CommandRouter,
    update: NormalizedTelegramUpdate,
) -> None:
    try:
        recorder = getattr(app.state, "telegram_recorder", None)
        if recorder is not None:
            await recorder.record_metadata(update)
            claim_update = getattr(recorder, "claim_update", None)
            if claim_update is not None and not await claim_update(update):
                logger.info("Skipping duplicate Telegram update_id=%s", update.update_id)
                return
        handled = await command_router.handle_update(update)
        if not handled:
            message_handler = getattr(app.state, "message_handler", None)
            if message_handler is not None:
                await message_handler.handle_update(update)
    except Exception:
        logger.exception("Telegram webhook update handling failed update_id=%s", update.update_id)
