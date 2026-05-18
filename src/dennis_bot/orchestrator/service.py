from __future__ import annotations

import logging
from dataclasses import dataclass, field
from inspect import signature
from pathlib import Path
from typing import Any

from dennis_bot.llm.types import ChatResponse
from dennis_bot.orchestrator.protocols import (
    ChatClientProtocol,
    InstagramActivityToolProtocol,
    KnowledgeServiceProtocol,
    MemoryServiceProtocol,
    RuntimeToolPlannerProtocol,
)
from dennis_bot.prompts.builder import build_conversation_messages
from dennis_bot.prompts.loaders import load_markdown_document

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IncomingMessage:
    text: str
    chat_id: int
    user_id: int | None = None
    message_id: int | None = None
    chat_type: str = "private"
    chat_title: str | None = None
    username: str | None = None
    is_trusted_group: bool = False
    active_knowledge_state: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OrchestratedResponse:
    text: str
    model: str
    recent_conversation_context: str
    memory_context: str
    knowledge_context: str
    runtime_tool_context: str
    prompt_messages_count: int


class ConversationOrchestrator:
    def __init__(
        self,
        *,
        llm_client: ChatClientProtocol,
        memory_service: MemoryServiceProtocol | None = None,
        knowledge_service: KnowledgeServiceProtocol | None = None,
        runtime_tool_planner: RuntimeToolPlannerProtocol | None = None,
        instagram_activity_tool: InstagramActivityToolProtocol | None = None,
        personality_path: Path | str = Path("config/personality/dennis-bot.md"),
        max_response_tokens: int | None = 450,
    ) -> None:
        self._llm_client = llm_client
        self._memory_service = memory_service
        self._knowledge_service = knowledge_service
        self._runtime_tool_planner = runtime_tool_planner
        self._instagram_activity_tool = instagram_activity_tool
        self._personality_path = Path(personality_path)
        self._max_response_tokens = max_response_tokens

    async def respond(self, incoming: IncomingMessage) -> OrchestratedResponse:
        personality = load_markdown_document(self._personality_path).content
        await self._record_user_message(incoming)
        recent_conversation_context = await self._retrieve_recent_conversation_context(incoming)
        contextual_followup = bool(
            recent_conversation_context and _is_contextual_followup(incoming.text)
        )
        memory_context = await self._retrieve_memory_context(incoming)
        knowledge_context = (
            ""
            if contextual_followup
            else await self._retrieve_knowledge_context(incoming)
        )
        runtime_tool_context = await self._retrieve_runtime_tool_context(incoming)
        messages = build_conversation_messages(
            user_text=incoming.text,
            personality=personality,
            recent_conversation_context=recent_conversation_context,
            memory_context=memory_context,
            knowledge_context=knowledge_context,
            runtime_tool_context=runtime_tool_context,
            telegram_metadata={
                **self._telegram_metadata(incoming),
                "is_contextual_followup": contextual_followup,
            },
        )
        response: ChatResponse = await self._llm_client.complete(
            messages,
            temperature=0.7,
            max_tokens=self._max_response_tokens,
        )
        await self._record_assistant_message(incoming.chat_id, response.content)
        return OrchestratedResponse(
            text=response.content,
            model=response.model,
            recent_conversation_context=recent_conversation_context,
            memory_context=memory_context,
            knowledge_context=knowledge_context,
            runtime_tool_context=runtime_tool_context,
            prompt_messages_count=len(messages),
        )

    async def _retrieve_recent_conversation_context(self, incoming: IncomingMessage) -> str:
        if not self._memory_service:
            return ""
        method = getattr(self._memory_service, "retrieve_recent_conversation", None)
        if method is None:
            return ""
        try:
            result = await _call_with_supported_kwargs(
                method,
                chat_id=incoming.chat_id,
                telegram_chat_id=incoming.chat_id,
                limit=8,
                exclude_message_id=incoming.message_id,
            )
        except Exception:
            logger.exception("Recent conversation retrieval failed")
            return ""
        return _context_to_text(result)

    async def _retrieve_memory_context(self, incoming: IncomingMessage) -> str:
        if not self._memory_service:
            return ""
        result = await _call_with_supported_kwargs(
            self._memory_service.retrieve_context,
            chat_id=incoming.chat_id,
            telegram_chat_id=incoming.chat_id,
            user_id=incoming.user_id,
            query=incoming.text,
            full_memory_access=incoming.is_trusted_group,
            metadata={"full_memory_access": incoming.is_trusted_group},
        )
        return _context_to_text(result)

    async def _retrieve_knowledge_context(self, incoming: IncomingMessage) -> str:
        if not self._knowledge_service:
            return ""
        return await self._knowledge_service.retrieve_context(
            query=incoming.text,
            chat_id=incoming.chat_id,
            state_name=incoming.active_knowledge_state,
        )

    async def _retrieve_runtime_tool_context(self, incoming: IncomingMessage) -> str:
        if self._runtime_tool_planner is None or self._instagram_activity_tool is None:
            return ""
        try:
            decision = await self._runtime_tool_planner.decide(user_text=incoming.text)
        except Exception:
            logger.exception("Runtime tool planner failed")
            return ""
        if not getattr(decision, "use_instagram_activity", False):
            return ""
        try:
            tool_context = await self._instagram_activity_tool.retrieve(limit=5)
        except Exception:
            logger.exception("Instagram activity cache tool failed")
            return ""
        reason = getattr(decision, "reason", "")
        if reason:
            return f"Tool selected by agent: instagram_activity_cache ({reason})\n{tool_context}"
        return f"Tool selected by agent: instagram_activity_cache\n{tool_context}"

    async def _record_user_message(self, incoming: IncomingMessage) -> None:
        if not self._memory_service:
            return
        await _call_with_supported_kwargs(
            self._memory_service.record_user_message,
            chat_id=incoming.chat_id,
            telegram_chat_id=incoming.chat_id,
            user_id=incoming.user_id,
            telegram_user_id=incoming.user_id,
            message_id=incoming.message_id,
            telegram_message_id=incoming.message_id,
            content=incoming.text,
        )

    async def _record_assistant_message(self, chat_id: int, content: str) -> None:
        if not self._memory_service:
            return
        await _call_with_supported_kwargs(
            self._memory_service.record_assistant_message,
            chat_id=chat_id,
            telegram_chat_id=chat_id,
            content=content,
        )

    def _telegram_metadata(self, incoming: IncomingMessage) -> dict[str, Any]:
        metadata = {
            "chat_id": incoming.chat_id,
            "chat_type": incoming.chat_type,
            "chat_title": incoming.chat_title,
            "user_id": incoming.user_id,
            "username": incoming.username,
            "message_id": incoming.message_id,
            "is_trusted_group": incoming.is_trusted_group,
            "active_knowledge_state": incoming.active_knowledge_state,
        }
        metadata.update(incoming.metadata)
        return metadata


async def _call_with_supported_kwargs(method: Any, **kwargs: Any) -> Any:
    parameters = signature(method).parameters
    accepted = {key: value for key, value in kwargs.items() if key in parameters}
    return await method(**accepted)


def _context_to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        context = value.get("context")
        if isinstance(context, str):
            return context
        if isinstance(context, list):
            return "\n".join(str(item) for item in context)
    return str(value)


def _is_contextual_followup(text: str) -> bool:
    normalized = " ".join(text.lower().strip().split())
    if not normalized:
        return False
    direct_phrases = {
        "tell me more",
        "do tell me more",
        "say more",
        "go on",
        "continue",
        "continue please",
        "more",
        "elaborate",
        "expand",
        "explain more",
        "what about that",
        "why so",
        "how so",
    }
    if normalized in direct_phrases:
        return True
    reference_terms = (
        "that",
        "it",
        "this",
        "those",
        "the above",
        "previous",
        "what you said",
        "you mentioned",
    )
    followup_verbs = (
        "tell me more",
        "more about",
        "explain",
        "elaborate",
        "expand",
        "continue",
        "go deeper",
        "share more",
    )
    return any(term in normalized for term in reference_terms) and any(
        verb in normalized for verb in followup_verbs
    )
