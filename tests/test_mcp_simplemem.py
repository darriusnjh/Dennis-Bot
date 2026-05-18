from __future__ import annotations

import pytest

from dennis_bot.mcp.simplemem import (
    JsonRpcMCPClient,
    MCPError,
    MissingSimpleMemToolsError,
    SimpleMemMCPClient,
    SimpleMemMCPConfig,
)


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    async def request(self, payload):
        self.requests.append(payload)
        return self.responses.pop(0)


def config() -> SimpleMemMCPConfig:
    return SimpleMemMCPConfig(
        url="https://simplemem.test/mcp",
        token="token",
        tenant_id="tenant",
        project="project",
    )


@pytest.mark.asyncio
async def test_json_rpc_client_sends_json_rpc_payload():
    transport = FakeTransport([{"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}])
    client = JsonRpcMCPClient(transport)

    result = await client.call("tools/list")

    assert result == {"ok": True}
    assert transport.requests == [{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}]


@pytest.mark.asyncio
async def test_json_rpc_client_raises_on_rpc_error():
    transport = FakeTransport([{"jsonrpc": "2.0", "id": 1, "error": {"message": "nope"}}])
    client = JsonRpcMCPClient(transport)

    with pytest.raises(MCPError, match="nope"):
        await client.call("tools/list")


@pytest.mark.asyncio
async def test_check_capabilities_maps_available_tools():
    tools = [
        {"name": "start_memory_session"},
        {"name": "record_message"},
        {"name": "retrieve_context"},
        {"name": "search_memory"},
        {"name": "finalize_memory_session"},
        {"name": "get_memory_stats"},
    ]
    transport = FakeTransport([{"jsonrpc": "2.0", "id": 1, "result": {"tools": tools}}])
    client = SimpleMemMCPClient(JsonRpcMCPClient(transport), config())

    capabilities = await client.check_capabilities()

    assert capabilities.start_session == "start_memory_session"
    assert capabilities.finalize_session == "finalize_memory_session"
    assert capabilities.stats == "get_memory_stats"


@pytest.mark.asyncio
async def test_check_capabilities_fails_when_lifecycle_tools_missing():
    transport = FakeTransport(
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {"tools": [{"name": "search_memory"}]},
            }
        ]
    )
    client = SimpleMemMCPClient(JsonRpcMCPClient(transport), config())

    with pytest.raises(MissingSimpleMemToolsError) as error:
        await client.check_capabilities()

    assert "start_session" in error.value.missing
    assert "finalize_session" in error.value.missing


@pytest.mark.asyncio
async def test_check_capabilities_supports_self_hosted_docker_tools():
    transport = FakeTransport(
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "tools": [
                        {"name": "memory_add"},
                        {"name": "memory_retrieve"},
                        {"name": "memory_query"},
                        {"name": "memory_stats"},
                    ]
                },
            }
        ]
    )
    client = SimpleMemMCPClient(JsonRpcMCPClient(transport), config())

    capabilities = await client.check_capabilities()

    assert capabilities.mode == "docker"
    assert capabilities.record_message == "memory_add"
    assert capabilities.retrieve_context == "memory_retrieve"
    assert capabilities.finalize_session.startswith("__simplemem_docker")


@pytest.mark.asyncio
async def test_docker_compat_record_message_maps_to_memory_add():
    transport = FakeTransport(
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "tools": [
                        {"name": "memory_add"},
                        {"name": "memory_retrieve"},
                        {"name": "memory_stats"},
                    ]
                },
            },
            {
                "jsonrpc": "2.0",
                "id": 2,
                "result": {"content": [{"type": "text", "text": "{\"success\": true}"}]},
            },
        ]
    )
    client = SimpleMemMCPClient(JsonRpcMCPClient(transport), config())

    await client.record_message(
        session_id="docker-simplemem:telegram-chat:123",
        role="user",
        content="remember I prefer short replies",
        metadata={"telegram_user_id": 456, "timestamp": "2026-05-15T01:00:00Z"},
    )

    call = transport.requests[1]
    assert call["params"]["name"] == "memory_add"
    assert call["params"]["arguments"] == {
        "speaker": "Telegram user 456",
        "content": "remember I prefer short replies",
        "timestamp": "2026-05-15T01:00:00Z",
    }


@pytest.mark.asyncio
async def test_docker_compat_retrieve_context_formats_memory_retrieve_results():
    transport = FakeTransport(
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "tools": [
                        {"name": "memory_add"},
                        {"name": "memory_retrieve"},
                    ]
                },
            },
            {
                "jsonrpc": "2.0",
                "id": 2,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "{\"results\":[{\"content\":\"User prefers short replies\","
                                "\"timestamp\":\"2026-05-15T01:00:00Z\","
                                "\"topic\":\"preferences\"}],\"total\":1}"
                            ),
                        }
                    ]
                },
            },
        ]
    )
    client = SimpleMemMCPClient(JsonRpcMCPClient(transport), config())

    context = await client.retrieve_context("reply style", chat_id=123, limit=3)

    assert transport.requests[1]["params"]["name"] == "memory_retrieve"
    assert transport.requests[1]["params"]["arguments"] == {"query": "reply style", "top_k": 3}
    assert "User prefers short replies" in context["context"]


@pytest.mark.asyncio
async def test_tool_call_includes_tenant_and_project():
    transport = FakeTransport(
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "tools": [
                        {"name": "start_memory_session"},
                        {"name": "record_message"},
                        {"name": "retrieve_context"},
                        {"name": "search_memory"},
                        {"name": "finalize_memory_session"},
                    ]
                },
            },
            {"jsonrpc": "2.0", "id": 2, "result": {"session_id": "s1"}},
        ]
    )
    client = SimpleMemMCPClient(JsonRpcMCPClient(transport), config())

    await client.start_session(chat_id=123)

    assert transport.requests[1]["method"] == "tools/call"
    assert transport.requests[1]["params"]["name"] == "start_memory_session"
    assert transport.requests[1]["params"]["arguments"]["tenant_id"] == "tenant"
    assert transport.requests[1]["params"]["arguments"]["project"] == "project"


@pytest.mark.asyncio
async def test_tool_call_unwraps_mcp_structured_content():
    transport = FakeTransport(
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "tools": [
                        {"name": "start_memory_session"},
                        {"name": "record_message"},
                        {"name": "retrieve_context"},
                        {"name": "search_memory"},
                        {"name": "finalize_memory_session"},
                    ]
                },
            },
            {
                "jsonrpc": "2.0",
                "id": 2,
                "result": {"structuredContent": {"session_id": "s1"}},
            },
        ]
    )
    client = SimpleMemMCPClient(JsonRpcMCPClient(transport), config())

    result = await client.start_session(chat_id=123)

    assert result == {"session_id": "s1"}
