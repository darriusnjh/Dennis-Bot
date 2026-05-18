from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from dennis_bot.config import Settings
from dennis_bot.mcp.simplemem import JsonObject, SimpleMemMCPClient, SimpleMemMCPConfig

MessageRole = Literal["user", "assistant"]
SessionStatus = Literal["active", "finalizing", "finalized", "failed"]


@dataclass
class MemorySessionRecord:
    id: str
    telegram_chat_id: int
    simplemem_tenant_id: str
    simplemem_project: str
    simplemem_memory_session_id: str
    message_count: int
    max_message_count: int
    status: SessionStatus
    started_at: datetime
    finalized_at: datetime | None = None
    finalization_report_ref: str | None = None
    error_message: str | None = None


@dataclass
class ConversationMessageRecord:
    telegram_chat_id: int
    direction: Literal["inbound", "outbound"]
    content: str
    simplemem_session_id: str | None
    telegram_user_id: int | None = None
    telegram_message_id: int | None = None
    message_type: str = "text"
    included_in_simplemem: bool = True
    memory_extraction_status: Literal["pending", "extracted", "skipped", "failed"] = "extracted"
    metadata: dict[str, Any] | None = None


class MemorySessionRepository(Protocol):
    async def get_active_session(self, telegram_chat_id: int) -> MemorySessionRecord | None:
        ...

    async def create_session(
        self,
        telegram_chat_id: int,
        simplemem_tenant_id: str,
        simplemem_project: str,
        simplemem_memory_session_id: str,
        max_message_count: int,
    ) -> MemorySessionRecord:
        ...

    async def increment_message_count(self, session_id: str) -> int:
        ...

    async def mark_session_finalizing(self, session_id: str) -> None:
        ...

    async def mark_session_finalized(
        self,
        session_id: str,
        finalized_at: datetime,
        finalization_report_ref: str | None,
    ) -> None:
        ...

    async def mark_session_failed(self, session_id: str, error_message: str) -> None:
        ...

    async def record_conversation_message(self, message: ConversationMessageRecord) -> None:
        ...

    async def list_recent_conversation(
        self,
        telegram_chat_id: int,
        *,
        limit: int = 8,
    ) -> list[ConversationMessageRecord]:
        ...


class DurableMemoryRepository(Protocol):
    async def add(
        self,
        *,
        scope: str,
        content: str,
        owner_user_id: int | None = None,
        chat_id: int | None = None,
        simplemem_tenant_id: str | None = None,
        simplemem_session_id: str | None = None,
        simplemem_entry_id: str | None = None,
        tags: str | None = None,
        importance: float | None = None,
        confidence: float | None = None,
        sensitivity: str | None = None,
        source_message_id: int | None = None,
    ) -> Any:
        ...

    async def list(
        self,
        *,
        scope: str | None = None,
        chat_id: int | None = None,
        owner_user_id: int | None = None,
        limit: int = 50,
    ) -> list[Any]:
        ...

    async def search(self, query: str, *, chat_id: int | None = None, limit: int = 20) -> list[Any]:
        ...

    async def soft_delete(self, memory_id: int) -> None:
        ...


class SimpleMemClientProtocol(Protocol):
    async def health_check(self) -> dict[str, Any]:
        ...

    async def start_session(self, chat_id: int, metadata: JsonObject | None = None) -> JsonObject:
        ...

    async def record_message(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: JsonObject | None = None,
    ) -> JsonObject:
        ...

    async def retrieve_context(
        self,
        query: str,
        chat_id: int | None = None,
        limit: int = 8,
        metadata: JsonObject | None = None,
    ) -> JsonObject:
        ...

    async def search_memory(self, query: str, limit: int = 10) -> JsonObject:
        ...

    async def finalize_session(self, session_id: str, reason: str = "message_limit") -> JsonObject:
        ...

    async def stats(self) -> JsonObject:
        ...


class MemoryService:
    def __init__(
        self,
        simplemem: SimpleMemClientProtocol,
        repository: MemorySessionRepository,
        tenant_id: str,
        project: str,
        max_session_messages: int = 30,
        memory_repository: DurableMemoryRepository | None = None,
    ) -> None:
        if max_session_messages < 1:
            raise ValueError("max_session_messages must be at least 1")
        self._simplemem = simplemem
        self._repository = repository
        self._tenant_id = tenant_id
        self._project = project
        self._max_session_messages = max_session_messages
        self._memory_repository = memory_repository

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        repository: MemorySessionRepository,
        memory_repository: DurableMemoryRepository | None = None,
    ) -> MemoryService:
        config = SimpleMemMCPConfig(
            url=settings.simplemem_mcp_url,
            token=settings.simplemem_mcp_token,
            tenant_id=settings.simplemem_tenant_id,
            project=settings.simplemem_project,
        )
        client = SimpleMemMCPClient.over_http(config)
        return cls(
            simplemem=client,
            repository=repository,
            tenant_id=settings.simplemem_tenant_id,
            project=settings.simplemem_project,
            max_session_messages=settings.simplemem_max_session_messages,
            memory_repository=memory_repository,
        )

    async def health_check(self) -> dict[str, Any]:
        health = await self._simplemem.health_check()
        return {
            **health,
            "max_session_messages": self._max_session_messages,
        }

    async def record_user_message(
        self,
        telegram_chat_id: int,
        content: str,
        telegram_user_id: int | None = None,
        telegram_message_id: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> MemorySessionRecord:
        return await self._record_message(
            telegram_chat_id=telegram_chat_id,
            role="user",
            direction="inbound",
            content=content,
            telegram_user_id=telegram_user_id,
            telegram_message_id=telegram_message_id,
            metadata=metadata,
        )

    async def record_assistant_message(
        self,
        telegram_chat_id: int,
        content: str,
        telegram_message_id: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> MemorySessionRecord:
        return await self._record_message(
            telegram_chat_id=telegram_chat_id,
            role="assistant",
            direction="outbound",
            content=content,
            telegram_message_id=telegram_message_id,
            metadata=metadata,
        )

    async def retrieve_context(
        self,
        telegram_chat_id: int | None = None,
        query: str = "",
        limit: int = 8,
        metadata: dict[str, Any] | None = None,
        *,
        chat_id: int | None = None,
        user_id: int | None = None,
        full_memory_access: bool = False,
    ) -> JsonObject:
        resolved_chat_id = telegram_chat_id if telegram_chat_id is not None else chat_id
        if resolved_chat_id is None:
            raise ValueError("telegram_chat_id or chat_id is required")
        request_metadata = dict(metadata or {})
        request_metadata.setdefault("full_memory_access", full_memory_access)
        if user_id is not None:
            request_metadata.setdefault("telegram_user_id", user_id)
        return await self._simplemem.retrieve_context(
            query=query,
            chat_id=resolved_chat_id,
            limit=limit,
            metadata=request_metadata,
        )

    async def retrieve_recent_conversation(
        self,
        telegram_chat_id: int | None = None,
        *,
        chat_id: int | None = None,
        limit: int = 8,
        exclude_message_id: int | None = None,
    ) -> str:
        resolved_chat_id = telegram_chat_id if telegram_chat_id is not None else chat_id
        if resolved_chat_id is None:
            raise ValueError("telegram_chat_id or chat_id is required")
        list_method = getattr(self._repository, "list_recent_conversation", None)
        if list_method is None:
            return ""
        messages = [
            message
            for message in await list_method(resolved_chat_id, limit=limit)
            if not (
                exclude_message_id is not None
                and message.direction == "inbound"
                and message.telegram_message_id == exclude_message_id
            )
        ]
        return _format_recent_conversation(messages)

    async def search(self, query: str, limit: int = 10) -> JsonObject:
        return await self._simplemem.search_memory(query=query, limit=limit)

    async def add_memory(
        self,
        *,
        content: str,
        scope: str = "user_profile",
        owner_user_id: int | None = None,
        chat_id: int | None = None,
        tags: list[str] | tuple[str, ...] | None = None,
        importance: float | None = None,
        confidence: float | None = None,
        sensitivity: str | None = None,
        source_message_id: int | None = None,
    ) -> Any:
        if self._memory_repository is None:
            raise RuntimeError("Durable memory repository is not configured")
        classification = classify_sensitive_content(content)
        tag_set = list(dict.fromkeys([*(tags or ()), *classification["tags"]]))
        return await self._memory_repository.add(
            scope=scope,
            owner_user_id=owner_user_id,
            chat_id=chat_id,
            simplemem_tenant_id=self._tenant_id,
            content=content,
            tags=json.dumps(tag_set) if tag_set else None,
            importance=importance,
            confidence=confidence,
            sensitivity=sensitivity or classification["sensitivity"],
            source_message_id=source_message_id,
        )

    async def list_memories(
        self,
        *,
        scope: str | None = None,
        chat_id: int | None = None,
        owner_user_id: int | None = None,
        limit: int = 50,
    ) -> list[Any]:
        if self._memory_repository is None:
            raise RuntimeError("Durable memory repository is not configured")
        return await self._memory_repository.list(
            scope=scope,
            chat_id=chat_id,
            owner_user_id=owner_user_id,
            limit=limit,
        )

    async def search_memories(
        self,
        query: str,
        *,
        chat_id: int | None = None,
        limit: int = 20,
    ) -> list[Any]:
        if self._memory_repository is None:
            raise RuntimeError("Durable memory repository is not configured")
        return await self._memory_repository.search(query, chat_id=chat_id, limit=limit)

    async def delete_memory(self, memory_id: int) -> None:
        if self._memory_repository is None:
            raise RuntimeError("Durable memory repository is not configured")
        await self._memory_repository.soft_delete(memory_id)

    async def stats(self) -> JsonObject:
        return await self._simplemem.stats()

    async def finalize_active_session(
        self,
        telegram_chat_id: int,
        reason: str = "manual",
    ) -> MemorySessionRecord | None:
        session = await self._repository.get_active_session(telegram_chat_id)
        if session is None:
            return None
        await self._finalize_session(session, reason=reason)
        return session

    async def finalize_all_active_sessions(self, reason: str = "shutdown") -> list[MemorySessionRecord]:
        list_method = getattr(self._repository, "list_active_sessions", None)
        if list_method is None:
            list_method = getattr(self._repository, "list_active", None)
        if list_method is None:
            return []

        sessions = await list_method()
        finalized: list[MemorySessionRecord] = []
        for item in sessions:
            session = _coerce_session_record(item)
            if session is None:
                continue
            await self._finalize_session(session, reason=reason)
            finalized.append(session)
        return finalized

    async def _record_message(
        self,
        telegram_chat_id: int,
        role: MessageRole,
        direction: Literal["inbound", "outbound"],
        content: str,
        telegram_user_id: int | None = None,
        telegram_message_id: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> MemorySessionRecord:
        session = await self._get_or_start_session(telegram_chat_id)
        classification = classify_sensitive_content(content)
        message_metadata = {
            "telegram_chat_id": telegram_chat_id,
            "telegram_user_id": telegram_user_id,
            "telegram_message_id": telegram_message_id,
            **(metadata or {}),
        }
        if classification["sensitivity"]:
            message_metadata["sensitivity"] = classification["sensitivity"]
            message_metadata["sensitive_memory_tags"] = classification["tags"]

        included_in_simplemem = False
        simplemem_error: Exception | None = None
        if classification["skip_simplemem"]:
            memory_extraction_status: Literal["extracted", "skipped", "failed"] = "skipped"
            message_metadata["memory_extraction_status"] = "skipped"
        else:
            try:
                await self._simplemem.record_message(
                    session_id=session.simplemem_memory_session_id,
                    role=role,
                    content=content,
                    metadata=message_metadata,
                )
                included_in_simplemem = True
                memory_extraction_status = "extracted"
            except Exception as exc:
                simplemem_error = exc
                memory_extraction_status = "failed"
                message_metadata["memory_extraction_status"] = "failed"

        await self._repository.record_conversation_message(
            ConversationMessageRecord(
                telegram_chat_id=telegram_chat_id,
                direction=direction,
                content=content,
                simplemem_session_id=session.simplemem_memory_session_id,
                telegram_user_id=telegram_user_id,
                telegram_message_id=telegram_message_id,
                included_in_simplemem=included_in_simplemem,
                memory_extraction_status=memory_extraction_status,
                metadata=message_metadata,
            )
        )
        if simplemem_error is not None:
            await self._repository.mark_session_failed(session.id, str(simplemem_error))
            session.status = "failed"
            session.error_message = str(simplemem_error)
            return session
        if included_in_simplemem:
            new_count = await self._repository.increment_message_count(session.id)
            session.message_count = new_count
            if new_count >= session.max_message_count:
                await self._finalize_session(session, reason="message_limit")
        return session

    async def _get_or_start_session(self, telegram_chat_id: int) -> MemorySessionRecord:
        session = await self._repository.get_active_session(telegram_chat_id)
        if session is not None and session.message_count < session.max_message_count:
            return session
        if session is not None and session.message_count >= session.max_message_count:
            await self._finalize_session(session, reason="message_limit")

        result = await self._simplemem.start_session(
            chat_id=telegram_chat_id,
            metadata={"telegram_chat_id": telegram_chat_id},
        )
        simplemem_session_id = _extract_session_id(result)
        return await self._repository.create_session(
            telegram_chat_id=telegram_chat_id,
            simplemem_tenant_id=self._tenant_id,
            simplemem_project=self._project,
            simplemem_memory_session_id=simplemem_session_id,
            max_message_count=self._max_session_messages,
        )

    async def _finalize_session(self, session: MemorySessionRecord, reason: str) -> None:
        await self._repository.mark_session_finalizing(session.id)
        try:
            report = await self._simplemem.finalize_session(
                session_id=session.simplemem_memory_session_id,
                reason=reason,
            )
        except Exception as exc:
            await self._repository.mark_session_failed(session.id, str(exc))
            raise
        await self._repository.mark_session_finalized(
            session_id=session.id,
            finalized_at=datetime.now(UTC),
            finalization_report_ref=_extract_report_ref(report),
        )


def _extract_session_id(result: JsonObject) -> str:
    for key in ("session_id", "memory_session_id", "id"):
        value = result.get(key)
        if isinstance(value, str) and value:
            return value
    nested = result.get("session")
    if isinstance(nested, dict):
        for key in ("session_id", "memory_session_id", "id"):
            value = nested.get(key)
            if isinstance(value, str) and value:
                return value
    raise ValueError("SimpleMem start session response did not include a session id")


def _extract_report_ref(result: JsonObject) -> str | None:
    for key in ("finalization_report_ref", "report_ref", "report_id", "summary_id"):
        value = result.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _coerce_session_record(value: Any) -> MemorySessionRecord | None:
    if isinstance(value, MemorySessionRecord):
        return value
    if isinstance(value, dict):
        try:
            return MemorySessionRecord(
                id=str(value["id"]),
                telegram_chat_id=int(value["telegram_chat_id"]),
                simplemem_tenant_id=str(value["simplemem_tenant_id"]),
                simplemem_project=str(value["simplemem_project"]),
                simplemem_memory_session_id=str(value.get("simplemem_memory_session_id") or ""),
                message_count=int(value["message_count"]),
                max_message_count=int(value["max_message_count"]),
                status=value["status"],
                started_at=_parse_datetime(value.get("started_at")) or datetime.now(UTC),
                finalized_at=_parse_datetime(value.get("finalized_at")),
                finalization_report_ref=value.get("finalization_report_ref"),
                error_message=value.get("error_message"),
            )
        except (KeyError, TypeError, ValueError):
            return None
    return None


def _format_recent_conversation(messages: list[ConversationMessageRecord]) -> str:
    if not messages:
        return ""
    lines = [
        "Recent Telegram conversation in this chat, oldest to newest.",
        "Use this to resolve follow-up references like 'that event', 'it', 'those posts', or 'what you just said'.",
    ]
    for message in messages:
        content = " ".join(message.content.split())
        if not content:
            continue
        if len(content) > 700:
            content = content[:697].rstrip() + "..."
        speaker = "Dennis" if message.direction == "outbound" else "User"
        lines.append(f"- {speaker}: {content}")
    return "\n".join(lines) if len(lines) > 2 else ""


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


_SECRET_PATTERNS = (
    re.compile(r"\b(?:api[_-]?key|secret|token|password|passwd|webhook[_-]?secret)\b", re.IGNORECASE),
    re.compile(r"\b(?:sk|pk)_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\b[A-Za-z0-9_-]{24,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)


def classify_sensitive_content(content: str) -> dict[str, Any]:
    tags: list[str] = []
    for pattern in _SECRET_PATTERNS:
        if pattern.search(content):
            tags.append("secret")
            break
    sensitivity = "secret" if tags else None
    return {
        "sensitivity": sensitivity,
        "tags": tags,
        "skip_simplemem": sensitivity == "secret",
    }
