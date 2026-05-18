from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

import httpx

JsonObject = dict[str, Any]


class MCPError(RuntimeError):
    """Raised when an MCP JSON-RPC request fails."""


class MissingSimpleMemToolsError(MCPError):
    def __init__(self, missing: list[str], available_tools: set[str]) -> None:
        self.missing = missing
        self.available_tools = available_tools
        available = ", ".join(sorted(available_tools)) or "none"
        super().__init__(
            "SimpleMem MCP endpoint is missing required lifecycle tool(s): "
            f"{', '.join(missing)}. Available tools: {available}"
        )


class MCPTransport(Protocol):
    async def request(self, payload: JsonObject) -> JsonObject:
        """Send a JSON-RPC payload and return the decoded response."""


@dataclass(frozen=True)
class SimpleMemMCPConfig:
    url: str
    token: str
    tenant_id: str = "dennis-bot-global"
    project: str = "dennis-bot"
    timeout_seconds: float = 20.0

    def validate(self) -> None:
        missing: list[str] = []
        if not self.url:
            missing.append("SIMPLEMEM_MCP_URL")
        if not self.token:
            missing.append("SIMPLEMEM_MCP_TOKEN")
        if not self.tenant_id:
            missing.append("SIMPLEMEM_TENANT_ID")
        if not self.project:
            missing.append("SIMPLEMEM_PROJECT")
        if missing:
            raise MCPError(f"Missing SimpleMem MCP configuration: {', '.join(missing)}")


class HttpMCPTransport:
    """HTTP/Streamable HTTP JSON-RPC transport for MCP endpoints."""

    def __init__(self, config: SimpleMemMCPConfig) -> None:
        config.validate()
        self._url = config.url
        self._headers = {
            "Authorization": f"Bearer {config.token}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        self._timeout = config.timeout_seconds
        self._session_id: str | None = None

    async def request(self, payload: JsonObject) -> JsonObject:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            headers = dict(self._headers)
            if payload.get("method") != "initialize":
                await self._ensure_session(client)
                if self._session_id:
                    headers["Mcp-Session-Id"] = self._session_id
            response = await client.post(self._url, headers=headers, json=payload)
            response.raise_for_status()
            session_id = response.headers.get("Mcp-Session-Id")
            if session_id:
                self._session_id = session_id
            return _decode_response(response)

    async def _ensure_session(self, client: httpx.AsyncClient) -> None:
        if self._session_id is not None:
            return
        payload: JsonObject = {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "dennis-bot", "version": "0.1.0"},
            },
        }
        response = await client.post(self._url, headers=self._headers, json=payload)
        response.raise_for_status()
        session_id = response.headers.get("Mcp-Session-Id")
        if session_id:
            self._session_id = session_id
        _decode_response(response)


def _decode_response(response: httpx.Response) -> JsonObject:
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        for line in response.text.splitlines():
            if line.startswith("data:"):
                data = line.removeprefix("data:").strip()
                if data and data != "[DONE]":
                    decoded = json.loads(data)
                    if isinstance(decoded, dict):
                        return decoded
        raise MCPError("MCP stream response did not contain a JSON data event")
    decoded = response.json()
    if not isinstance(decoded, dict):
        raise MCPError("MCP response was not a JSON object")
    return decoded


class JsonRpcMCPClient:
    def __init__(self, transport: MCPTransport) -> None:
        self._transport = transport
        self._next_id = 1

    async def call(self, method: str, params: JsonObject | None = None) -> Any:
        request_id = self._next_id
        self._next_id += 1
        payload: JsonObject = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
        }
        if params is not None:
            payload["params"] = params

        response = await self._transport.request(payload)
        if response.get("error"):
            error = response["error"]
            message = (
                error.get("message", "MCP request failed")
                if isinstance(error, dict)
                else str(error)
            )
            raise MCPError(message)
        if "result" not in response:
            raise MCPError("MCP response did not include a result")
        return response["result"]


@dataclass(frozen=True)
class SimpleMemCapabilities:
    start_session: str
    record_message: str
    retrieve_context: str
    search_memory: str
    finalize_session: str
    stats: str | None = None
    mode: Literal["lifecycle", "docker"] = "lifecycle"


REQUIRED_TOOL_CANDIDATES: dict[str, tuple[str, ...]] = {
    "start_session": (
        "start_memory_session",
        "memory_session_start",
        "start_session",
        "session_start",
    ),
    "record_message": (
        "record_message",
        "record_memory_message",
        "add_message",
        "append_message",
    ),
    "retrieve_context": (
        "retrieve_context",
        "retrieve_memory_context",
        "get_context",
        "memory_context",
    ),
    "search_memory": (
        "search_memory",
        "memory_search",
        "search",
    ),
    "finalize_session": (
        "finalize_memory_session",
        "end_memory_session",
        "stop_memory_session",
        "finalize_session",
        "end_session",
    ),
}

OPTIONAL_STATS_CANDIDATES = (
    "get_memory_stats",
    "memory_stats",
    "get_stats",
    "health",
)

DOCKER_COMPAT_TOOLS = frozenset(
    {
        "memory_add",
        "memory_retrieve",
        "memory_query",
        "memory_stats",
    }
)

SYNTHETIC_START_SESSION_TOOL = "__simplemem_docker_start_session__"
SYNTHETIC_FINALIZE_SESSION_TOOL = "__simplemem_docker_finalize_session__"


class SimpleMemMCPClient:
    def __init__(
        self,
        rpc: JsonRpcMCPClient,
        config: SimpleMemMCPConfig,
        capabilities: SimpleMemCapabilities | None = None,
    ) -> None:
        config.validate()
        self._rpc = rpc
        self._config = config
        self._capabilities = capabilities

    @classmethod
    def over_http(cls, config: SimpleMemMCPConfig) -> SimpleMemMCPClient:
        return cls(JsonRpcMCPClient(HttpMCPTransport(config)), config)

    async def list_tools(self) -> set[str]:
        result = await self._rpc.call("tools/list")
        tools = result.get("tools", result) if isinstance(result, dict) else result
        if not isinstance(tools, list):
            raise MCPError("MCP tools/list result did not contain a tool list")

        names: set[str] = set()
        for tool in tools:
            if isinstance(tool, str):
                names.add(tool)
            elif isinstance(tool, dict) and isinstance(tool.get("name"), str):
                names.add(tool["name"])
        return names

    async def check_capabilities(self) -> SimpleMemCapabilities:
        available_tools = await self.list_tools()
        selected: dict[str, str] = {}
        missing: list[str] = []
        for capability, candidates in REQUIRED_TOOL_CANDIDATES.items():
            tool_name = _first_available(candidates, available_tools)
            if tool_name is None:
                missing.append(capability)
            else:
                selected[capability] = tool_name

        if missing:
            if _supports_docker_compat(available_tools):
                self._capabilities = SimpleMemCapabilities(
                    start_session=SYNTHETIC_START_SESSION_TOOL,
                    record_message="memory_add",
                    retrieve_context="memory_retrieve",
                    search_memory="memory_retrieve",
                    finalize_session=SYNTHETIC_FINALIZE_SESSION_TOOL,
                    stats="memory_stats" if "memory_stats" in available_tools else None,
                    mode="docker",
                )
                return self._capabilities
            raise MissingSimpleMemToolsError(missing, available_tools)

        self._capabilities = SimpleMemCapabilities(
            start_session=selected["start_session"],
            record_message=selected["record_message"],
            retrieve_context=selected["retrieve_context"],
            search_memory=selected["search_memory"],
            finalize_session=selected["finalize_session"],
            stats=_first_available(OPTIONAL_STATS_CANDIDATES, available_tools),
        )
        return self._capabilities

    async def health_check(self) -> dict[str, Any]:
        capabilities = await self.check_capabilities()
        return {
            "ok": True,
            "tenant_id": self._config.tenant_id,
            "project": self._config.project,
            "capabilities": capabilities.__dict__,
        }

    async def start_session(self, chat_id: int, metadata: JsonObject | None = None) -> JsonObject:
        capabilities = await self._require_capabilities()
        if capabilities.mode == "docker":
            return {
                "session_id": f"docker-simplemem:telegram-chat:{chat_id}",
                "compatibility_mode": "docker",
                "metadata": metadata or {},
            }
        return await self._call_tool(
            capabilities.start_session,
            {
                "tenant_id": self._config.tenant_id,
                "project": self._config.project,
                "external_session_key": f"telegram-chat:{chat_id}",
                "metadata": metadata or {},
            },
        )

    async def record_message(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: JsonObject | None = None,
    ) -> JsonObject:
        capabilities = await self._require_capabilities()
        if capabilities.mode == "docker":
            metadata = metadata or {}
            return await self._call_tool(
                capabilities.record_message,
                {
                    "speaker": _speaker_name(role, metadata),
                    "content": content,
                    "timestamp": _timestamp(metadata),
                },
            )
        return await self._call_tool(
            capabilities.record_message,
            {
                "tenant_id": self._config.tenant_id,
                "project": self._config.project,
                "session_id": session_id,
                "role": role,
                "content": content,
                "metadata": metadata or {},
            },
        )

    async def retrieve_context(
        self,
        query: str,
        chat_id: int | None = None,
        limit: int = 8,
        metadata: JsonObject | None = None,
    ) -> JsonObject:
        arguments: JsonObject = {
            "tenant_id": self._config.tenant_id,
            "project": self._config.project,
            "query": query,
            "limit": limit,
            "metadata": metadata or {},
        }
        if chat_id is not None:
            arguments["chat_id"] = chat_id
        capabilities = await self._require_capabilities()
        if capabilities.mode == "docker":
            result = await self._call_tool(
                capabilities.retrieve_context,
                {"query": query, "top_k": limit},
            )
            return {"context": _docker_context_text(result), **result}
        return await self._call_tool(capabilities.retrieve_context, arguments)

    async def search_memory(self, query: str, limit: int = 10) -> JsonObject:
        capabilities = await self._require_capabilities()
        if capabilities.mode == "docker":
            return await self._call_tool(
                capabilities.search_memory,
                {"query": query, "top_k": limit},
            )
        return await self._call_tool(
            capabilities.search_memory,
            {
                "tenant_id": self._config.tenant_id,
                "project": self._config.project,
                "query": query,
                "limit": limit,
            },
        )

    async def finalize_session(self, session_id: str, reason: str = "message_limit") -> JsonObject:
        capabilities = await self._require_capabilities()
        if capabilities.mode == "docker":
            return {
                "ok": True,
                "session_id": session_id,
                "reason": reason,
                "compatibility_mode": "docker",
            }
        return await self._call_tool(
            capabilities.finalize_session,
            {
                "tenant_id": self._config.tenant_id,
                "project": self._config.project,
                "session_id": session_id,
                "reason": reason,
            },
        )

    async def stats(self) -> JsonObject:
        capabilities = await self._require_capabilities()
        if capabilities.stats is None:
            return {"ok": True, "stats_supported": False}
        return await self._call_tool(
            capabilities.stats,
            {"tenant_id": self._config.tenant_id, "project": self._config.project},
        )

    async def _call_tool(self, name: str, arguments: JsonObject) -> JsonObject:
        result = await self._rpc.call("tools/call", {"name": name, "arguments": arguments})
        if isinstance(result, dict):
            if result.get("isError"):
                raise MCPError(_extract_tool_error(result))
            structured = result.get("structuredContent") or result.get("structured_content")
            if isinstance(structured, dict):
                return structured
            text_content = _extract_text_content(result)
            if text_content:
                try:
                    decoded = json.loads(text_content)
                except json.JSONDecodeError:
                    return {"content": text_content}
                if isinstance(decoded, dict):
                    return decoded
                return {"content": decoded}
            return result
        return {"result": result}

    async def _require_capabilities(self) -> SimpleMemCapabilities:
        if self._capabilities is None:
            return await self.check_capabilities()
        return self._capabilities


def _first_available(candidates: tuple[str, ...], available_tools: set[str]) -> str | None:
    for candidate in candidates:
        if candidate in available_tools:
            return candidate
    return None


def _supports_docker_compat(available_tools: set[str]) -> bool:
    required = {"memory_add", "memory_retrieve"}
    return required.issubset(available_tools) and bool(available_tools.intersection(DOCKER_COMPAT_TOOLS))


def _speaker_name(role: str, metadata: JsonObject) -> str:
    if role == "assistant":
        return "Dennis Bot"
    username = metadata.get("username")
    if isinstance(username, str) and username:
        return username
    user_id = metadata.get("telegram_user_id")
    if user_id is not None:
        return f"Telegram user {user_id}"
    return "Telegram user"


def _timestamp(metadata: JsonObject) -> str:
    value = metadata.get("timestamp") or metadata.get("created_at")
    if isinstance(value, str) and value:
        return value
    return datetime.now(UTC).isoformat()


def _docker_context_text(result: JsonObject) -> str:
    results = result.get("results")
    if not isinstance(results, list):
        return ""
    lines: list[str] = []
    for index, item in enumerate(results, start=1):
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if not content:
            continue
        timestamp = item.get("timestamp")
        topic = item.get("topic")
        suffix_parts = [str(part) for part in (timestamp, topic) if part]
        suffix = f" ({', '.join(suffix_parts)})" if suffix_parts else ""
        lines.append(f"{index}. {content}{suffix}")
    return "\n".join(lines)


def _extract_tool_error(result: JsonObject) -> str:
    text = _extract_text_content(result)
    if text:
        return text
    return "SimpleMem MCP tool call failed"


def _extract_text_content(result: JsonObject) -> str | None:
    content = result.get("content")
    if not isinstance(content, list):
        return None
    parts: list[str] = []
    for item in content:
        if (
            isinstance(item, dict)
            and item.get("type") == "text"
            and isinstance(item.get("text"), str)
        ):
            parts.append(item["text"])
    if not parts:
        return None
    return "\n".join(parts)
