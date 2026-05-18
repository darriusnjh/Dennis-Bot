from __future__ import annotations

from typing import Protocol

from dennis_bot.llm.types import ChatMessage, ChatResponse


class ChatClientProtocol(Protocol):
    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> ChatResponse: ...


class MemoryServiceProtocol(Protocol):
    async def retrieve_context(
        self,
        *,
        chat_id: int,
        user_id: int | None,
        query: str,
        full_memory_access: bool = False,
    ) -> str: ...

    async def retrieve_recent_conversation(
        self,
        *,
        chat_id: int,
        limit: int = 8,
        exclude_message_id: int | None = None,
    ) -> str: ...

    async def record_user_message(
        self,
        *,
        chat_id: int,
        user_id: int | None,
        message_id: int | None,
        content: str,
    ) -> None: ...

    async def record_assistant_message(
        self,
        *,
        chat_id: int,
        content: str,
    ) -> None: ...


class KnowledgeServiceProtocol(Protocol):
    async def retrieve_context(
        self,
        *,
        query: str,
        chat_id: int | None = None,
        state_name: str | None = None,
    ) -> str: ...


class RuntimeToolPlannerProtocol(Protocol):
    async def decide(self, *, user_text: str): ...


class InstagramActivityToolProtocol(Protocol):
    async def retrieve(self, *, limit: int | None = None) -> str: ...
