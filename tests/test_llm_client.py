from __future__ import annotations

import json

import httpx
import pytest

from dennis_bot.llm.client import OpenAIChatClient
from dennis_bot.llm.types import ChatMessage


@pytest.mark.asyncio
async def test_openai_chat_client_posts_compatible_payload() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers["authorization"]
        captured["payload"] = request.read()
        return httpx.Response(
            200,
            json={
                "model": "test-model",
                "choices": [{"message": {"content": "Can, steady."}}],
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = OpenAIChatClient(
            api_key="secret-key",
            model="test-model",
            base_url="https://llm.example/v1",
            client=http_client,
        )
        response = await client.complete([ChatMessage(role="user", content="hello")])

    assert response.content == "Can, steady."
    assert captured["url"] == "https://llm.example/v1/chat/completions"
    assert captured["auth"] == "Bearer secret-key"
    payload = json.loads(captured["payload"])
    assert payload["model"] == "test-model"
    assert payload["messages"] == [{"role": "user", "content": "hello"}]
    assert payload["temperature"] == 0.7


@pytest.mark.asyncio
async def test_openai_chat_client_uses_gpt5_chat_completions_payload() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = request.read()
        return httpx.Response(
            200,
            json={
                "model": "gpt-5-nano",
                "choices": [{"message": {"content": "ok"}}],
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = OpenAIChatClient(
            api_key="secret-key",
            model="gpt-5-nano",
            client=http_client,
        )
        response = await client.complete(
            [ChatMessage(role="user", content="hello")],
            temperature=0.7,
            max_tokens=700,
        )

    assert response.content == "ok"
    payload = json.loads(captured["payload"])
    assert payload["model"] == "gpt-5-nano"
    assert "temperature" not in payload
    assert "max_tokens" not in payload
    assert payload["max_completion_tokens"] == 1200


@pytest.mark.asyncio
async def test_openai_chat_client_retries_empty_gpt5_length_response_without_limit() -> None:
    captured_payloads: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.read())
        captured_payloads.append(payload)
        if len(captured_payloads) == 1:
            return httpx.Response(
                200,
                json={
                    "model": "gpt-5-nano",
                    "choices": [
                        {
                            "message": {"content": ""},
                            "finish_reason": "length",
                        }
                    ],
                },
            )
        return httpx.Response(
            200,
            json={
                "model": "gpt-5-nano",
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = OpenAIChatClient(
            api_key="secret-key",
            model="gpt-5-nano",
            client=http_client,
        )
        response = await client.complete(
            [ChatMessage(role="user", content="hello")],
            max_tokens=700,
        )

    assert response.content == "ok"
    assert "max_completion_tokens" in captured_payloads[0]
    assert captured_payloads[0]["max_completion_tokens"] == 1200
    assert "max_completion_tokens" not in captured_payloads[1]
