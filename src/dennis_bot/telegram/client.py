from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx


class TelegramApiError(RuntimeError):
    def __init__(self, method: str, description: str, error_code: int | None = None) -> None:
        self.method = method
        self.description = description
        self.error_code = error_code
        super().__init__(f"Telegram {method} failed: {description}")


class TelegramClient:
    def __init__(
        self,
        bot_token: str,
        *,
        client: httpx.AsyncClient | None = None,
        base_url: str = "https://api.telegram.org",
    ) -> None:
        self.bot_token = bot_token
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=10.0)
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def send_message(
        self,
        chat_id: int | str,
        text: str,
        *,
        reply_to_message_id: int | None = None,
        parse_mode: str | None = None,
        disable_web_page_preview: bool | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if reply_to_message_id is not None:
            payload["reply_to_message_id"] = reply_to_message_id
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if disable_web_page_preview is not None:
            payload["disable_web_page_preview"] = disable_web_page_preview
        return await self._post("sendMessage", payload)

    async def send_sticker(
        self,
        chat_id: int | str,
        sticker: str,
        *,
        reply_to_message_id: int | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"chat_id": chat_id, "sticker": sticker}
        if reply_to_message_id is not None:
            payload["reply_to_message_id"] = reply_to_message_id
        return await self._post("sendSticker", payload)

    async def get_sticker_set(self, name: str) -> dict[str, Any]:
        return await self._post("getStickerSet", {"name": name})

    async def get_me(self) -> dict[str, Any]:
        return await self._post("getMe", {})

    async def get_updates(
        self,
        *,
        offset: int | None = None,
        limit: int | None = None,
        timeout: int | None = None,
        allowed_updates: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {}
        if offset is not None:
            payload["offset"] = offset
        if limit is not None:
            payload["limit"] = limit
        if timeout is not None:
            payload["timeout"] = timeout
        if allowed_updates is not None:
            payload["allowed_updates"] = allowed_updates

        request_timeout = (timeout or 0) + 10 if timeout else None
        result = await self._post_raw("getUpdates", payload, timeout=request_timeout)
        if not isinstance(result, list):
            raise TelegramApiError("getUpdates", "expected list result")
        return [item for item in result if isinstance(item, dict)]

    async def set_webhook(
        self,
        url: str,
        *,
        secret_token: str | None = None,
        allowed_updates: list[str] | None = None,
        drop_pending_updates: bool | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"url": url}
        if secret_token:
            payload["secret_token"] = secret_token
        if allowed_updates:
            payload["allowed_updates"] = allowed_updates
        if drop_pending_updates is not None:
            payload["drop_pending_updates"] = drop_pending_updates
        return await self._post("setWebhook", payload)

    async def delete_webhook(self, *, drop_pending_updates: bool = False) -> dict[str, Any]:
        return await self._post("deleteWebhook", {"drop_pending_updates": drop_pending_updates})

    async def _post(
        self,
        method: str,
        payload: Mapping[str, Any],
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        result = await self._post_raw(method, payload, timeout=timeout)
        return result if isinstance(result, dict) else {"result": result}

    async def _post_raw(
        self,
        method: str,
        payload: Mapping[str, Any],
        *,
        timeout: float | None = None,
    ) -> Any:
        url = f"{self.base_url}/bot{self.bot_token}/{method}"
        response = await self._client.post(url, json=dict(payload), timeout=timeout)
        try:
            data = response.json()
        except ValueError:
            response.raise_for_status()
            raise TelegramApiError(method, "invalid JSON response")
        if not data.get("ok"):
            raise TelegramApiError(
                method,
                str(data.get("description", "unknown error")),
                data.get("error_code"),
            )
        response.raise_for_status()
        return data.get("result")
