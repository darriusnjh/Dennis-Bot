from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request, status

from dennis_bot.config import Settings
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
        recorder = getattr(request.app.state, "telegram_recorder", None)
        if recorder is not None:
            await recorder.record_metadata(update)
        handled = await command_router.handle_update(update)
        if not handled:
            message_handler = getattr(request.app.state, "message_handler", None)
            if message_handler is not None:
                handled = await message_handler.handle_update(update)
        return {"ok": True, "handled": handled}

    return router
