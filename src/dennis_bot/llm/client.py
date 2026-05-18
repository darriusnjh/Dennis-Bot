from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

import httpx

from dennis_bot.llm.types import ChatMessage, ChatResponse

GPT5_MIN_COMPLETION_TOKENS = 1200


class LLMClientError(RuntimeError):
    """Raised when the configured chat-completions provider fails."""


class OpenAIChatClient:
    """Small OpenAI-compatible chat completions client.

    The client intentionally depends only on the OpenAI wire format so it can be
    pointed at OpenAI, an OpenAI-compatible gateway, or a local model server.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str | None = None,
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._client = client

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> ChatResponse:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [message.to_openai() for message in messages],
        }
        if not _uses_gpt5_chat_completions_constraints(self._model):
            payload["temperature"] = temperature
        if max_tokens is not None:
            token_limit_param = (
                "max_completion_tokens"
                if _uses_gpt5_chat_completions_constraints(self._model)
                else "max_tokens"
            )
            payload[token_limit_param] = (
                max(max_tokens, GPT5_MIN_COMPLETION_TOKENS)
                if token_limit_param == "max_completion_tokens"
                else max_tokens
            )

        raw = await self._post_with_retries(payload)
        content = _extract_message_content(raw)
        if (
            not content.strip()
            and _uses_gpt5_chat_completions_constraints(self._model)
            and raw.get("choices", [{}])[0].get("finish_reason") == "length"
            and "max_completion_tokens" in payload
        ):
            retry_payload = dict(payload)
            retry_payload.pop("max_completion_tokens", None)
            raw = await self._post_with_retries(retry_payload)
            content = _extract_message_content(raw)
        if not content.strip():
            raise LLMClientError("Chat provider returned empty message content")

        return ChatResponse(content=content, model=str(raw.get("model") or self._model), raw=raw)

    async def _post_with_retries(self, payload: dict[str, Any]) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                return await self._post(payload)
            except (httpx.TimeoutException, httpx.TransportError, LLMClientError) as exc:
                last_error = exc
                if attempt >= self._max_retries:
                    break
                await asyncio.sleep(0.25 * (2**attempt))
        raise LLMClientError("Chat provider request failed") from last_error

    async def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        if self._client:
            response = await self._client.post(
                f"{self._base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=self._timeout_seconds,
            )
        else:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.post(
                    f"{self._base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                )

        if response.status_code >= 400:
            raise LLMClientError(f"Chat provider returned HTTP {response.status_code}")
        return response.json()


def _uses_gpt5_chat_completions_constraints(model: str) -> bool:
    return model.lower().startswith("gpt-5")


def _extract_message_content(raw: dict[str, Any]) -> str:
    try:
        return raw["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMClientError("Chat provider returned an invalid response shape") from exc
