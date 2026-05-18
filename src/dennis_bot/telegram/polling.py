from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from dennis_bot.telegram.client import TelegramClient
from dennis_bot.telegram.models import NormalizedTelegramUpdate
from dennis_bot.telegram.normalize import normalize_update

logger = logging.getLogger(__name__)

TelegramUpdateHandler = Callable[[NormalizedTelegramUpdate], Awaitable[Any]]


class TelegramPollingRunner:
    def __init__(
        self,
        client: TelegramClient,
        update_handler: TelegramUpdateHandler,
        *,
        bot_username: str | None = None,
        bot_user_id: int | None = None,
        offset: int | None = None,
        limit: int = 100,
        timeout: int = 30,
        allowed_updates: list[str] | None = None,
        error_sleep_seconds: float = 5.0,
    ) -> None:
        self.client = client
        self.update_handler = update_handler
        self.bot_username = bot_username
        self.bot_user_id = bot_user_id
        self.offset = offset
        self.limit = limit
        self.timeout = timeout
        self.allowed_updates = allowed_updates
        self.error_sleep_seconds = error_sleep_seconds

    async def poll_once(self) -> int:
        updates = await self.client.get_updates(
            offset=self.offset,
            limit=self.limit,
            timeout=self.timeout,
            allowed_updates=self.allowed_updates,
        )
        handled_count = 0
        for raw_update in updates:
            normalized = normalize_update(
                raw_update,
                bot_username=self.bot_username,
                bot_user_id=self.bot_user_id,
            )
            if normalized is not None:
                await self.update_handler(normalized)
                handled_count += 1
            self.offset = int(raw_update.get("update_id", 0)) + 1
        return handled_count

    async def run_forever(self, *, stop_event: asyncio.Event | None = None) -> None:
        while stop_event is None or not stop_event.is_set():
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Telegram polling failed")
                if stop_event is None:
                    await asyncio.sleep(self.error_sleep_seconds)
                else:
                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=self.error_sleep_seconds)
                    except TimeoutError:
                        pass
