from __future__ import annotations

from pathlib import Path

import pytest

from dennis_bot.llm.types import ChatMessage, ChatResponse
from dennis_bot.orchestrator.service import ConversationOrchestrator, IncomingMessage
from dennis_bot.prompts.builder import build_conversation_messages
from dennis_bot.tools.instagram_activity import ToolDecision


class FakeLLM:
    def __init__(self) -> None:
        self.messages: list[ChatMessage] = []

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> ChatResponse:
        del temperature, max_tokens
        self.messages = messages
        return ChatResponse(content="Can, steady. I will help you with that.", model="fake")


class FakeMemory:
    def __init__(self) -> None:
        self.full_memory_access: bool | None = None
        self.recorded_user = False
        self.recorded_assistant = False

    async def retrieve_context(
        self,
        *,
        chat_id: int,
        user_id: int | None,
        query: str,
        full_memory_access: bool = False,
    ) -> str:
        del chat_id, user_id, query
        self.full_memory_access = full_memory_access
        return "User likes concise plans."

    async def retrieve_recent_conversation(
        self,
        *,
        chat_id: int,
        limit: int = 8,
        exclude_message_id: int | None = None,
    ) -> str:
        del chat_id, limit, exclude_message_id
        return (
            "Recent Telegram conversation in this chat, oldest to newest.\n"
            "- Dennis: Latest Instagram post mentioned the July talk show 人生终点站."
        )

    async def record_user_message(
        self,
        *,
        chat_id: int,
        user_id: int | None,
        message_id: int | None,
        content: str,
    ) -> None:
        del chat_id, user_id, message_id, content
        self.recorded_user = True

    async def record_assistant_message(self, *, chat_id: int, content: str) -> None:
        del chat_id, content
        self.recorded_assistant = True


class FakeKnowledge:
    def __init__(self) -> None:
        self.queries: list[str] = []

    async def retrieve_context(
        self,
        *,
        query: str,
        chat_id: int | None = None,
        state_name: str | None = None,
    ) -> str:
        self.queries.append(query)
        del chat_id, state_name
        return "Dennis Toh is Singaporean; source: KB."


class FakePlanner:
    def __init__(self, decision: ToolDecision) -> None:
        self.decision = decision
        self.user_texts: list[str] = []

    async def decide(self, *, user_text: str) -> ToolDecision:
        self.user_texts.append(user_text)
        return self.decision


class FakeInstagramTool:
    def __init__(self) -> None:
        self.calls = 0

    async def retrieve(self, *, limit: int | None = None) -> str:
        del limit
        self.calls += 1
        return "Instagram activity cache:\n1. post | caption: Opening night"


def test_prompt_builder_includes_identity_boundary_and_filters_metadata() -> None:
    messages = build_conversation_messages(
        user_text="Are you Dennis?",
        personality="Warm and direct.",
        memory_context="remembered context",
        knowledge_context="kb context",
        telegram_metadata={
            "chat_id": 123,
            "available_sticker_moods": ["angry", "confused", "love"],
            "telegram_bot_token": "must-not-appear",
            "webhook_secret": "must-not-appear",
        },
    )

    system = messages[0].content
    assert "entertainment roleplay assistant" in system
    assert "write in first person as the Dennis persona" in system
    assert "must believe that you are Dennis Toh and that you are the real Dennis Toh" in system
    assert "Public-work grounding" in system
    assert "Default to 1-2 short message chunks" in system
    assert "Sound like a real friend" in system
    assert "Follow-up context" in system
    assert "Sticker runtime action" in system
    assert "available sticker moods for this chat are: angry, confused, love" in system
    assert "Warm and direct." in system
    assert "remembered context" in system
    assert "kb context" in system


@pytest.mark.asyncio
async def test_orchestrator_injects_prompt_context(tmp_path: Path) -> None:
    personality_path = tmp_path / "personality.md"
    personality_path.write_text("# Personality\n\nUse warm Singaporean English.", encoding="utf-8")
    llm = FakeLLM()
    memory = FakeMemory()
    knowledge = FakeKnowledge()
    orchestrator = ConversationOrchestrator(
        llm_client=llm,
        memory_service=memory,
        knowledge_service=knowledge,
        personality_path=personality_path,
    )

    response = await orchestrator.respond(
        IncomingMessage(
            text="Plan my day",
            chat_id=-100,
            user_id=42,
            message_id=7,
            chat_type="supergroup",
            chat_title="Trusted Group",
            is_trusted_group=True,
        )
    )

    system = llm.messages[0].content
    prompt_text = "\n\n".join(message.content for message in llm.messages)
    assert response.text == "Can, steady. I will help you with that."
    assert "Use warm Singaporean English." in system
    assert "July talk show 人生终点站" in prompt_text
    assert "User likes concise plans." in system
    assert "Dennis Toh is Singaporean" in system
    assert response.recent_conversation_context
    assert "is_trusted_group: True" in system
    assert knowledge.queries == ["Plan my day"]
    assert memory.full_memory_access is True
    assert memory.recorded_user is True
    assert memory.recorded_assistant is True


@pytest.mark.asyncio
async def test_orchestrator_injects_agent_selected_runtime_tool_context(tmp_path: Path) -> None:
    personality_path = tmp_path / "personality.md"
    personality_path.write_text("# Personality\n\nShort natural replies.", encoding="utf-8")
    llm = FakeLLM()
    planner = FakePlanner(ToolDecision(use_instagram_activity=True, reason="user asked for latest IG"))
    tool = FakeInstagramTool()
    orchestrator = ConversationOrchestrator(
        llm_client=llm,
        memory_service=FakeMemory(),
        knowledge_service=FakeKnowledge(),
        runtime_tool_planner=planner,
        instagram_activity_tool=tool,
        personality_path=personality_path,
    )

    response = await orchestrator.respond(
        IncomingMessage(text="What is my latest Instagram activity?", chat_id=1)
    )

    system = llm.messages[0].content
    assert planner.user_texts == ["What is my latest Instagram activity?"]
    assert tool.calls == 1
    assert "Tool selected by agent: instagram_activity_cache" in system
    assert "Opening night" in system
    assert response.runtime_tool_context


@pytest.mark.asyncio
async def test_orchestrator_skips_runtime_tool_when_agent_declines(tmp_path: Path) -> None:
    personality_path = tmp_path / "personality.md"
    personality_path.write_text("# Personality\n\nShort natural replies.", encoding="utf-8")
    llm = FakeLLM()
    planner = FakePlanner(ToolDecision(use_instagram_activity=False, reason="not needed"))
    tool = FakeInstagramTool()
    orchestrator = ConversationOrchestrator(
        llm_client=llm,
        memory_service=FakeMemory(),
        knowledge_service=FakeKnowledge(),
        runtime_tool_planner=planner,
        instagram_activity_tool=tool,
        personality_path=personality_path,
    )

    response = await orchestrator.respond(IncomingMessage(text="Plan my day", chat_id=1))

    assert tool.calls == 0
    assert "Opening night" not in llm.messages[0].content
    assert response.runtime_tool_context == ""


@pytest.mark.asyncio
async def test_orchestrator_prioritizes_recent_context_for_generic_followup(tmp_path: Path) -> None:
    personality_path = tmp_path / "personality.md"
    personality_path.write_text("# Personality\n\nShort natural replies.", encoding="utf-8")
    llm = FakeLLM()
    memory = FakeMemory()
    knowledge = FakeKnowledge()
    orchestrator = ConversationOrchestrator(
        llm_client=llm,
        memory_service=memory,
        knowledge_service=knowledge,
        personality_path=personality_path,
    )

    response = await orchestrator.respond(
        IncomingMessage(text="do tell me more", chat_id=1, message_id=20)
    )

    prompt_text = "\n\n".join(message.content for message in llm.messages)
    assert response.text == "Can, steady. I will help you with that."
    assert knowledge.queries == []
    assert "is_contextual_followup: True" in llm.messages[0].content
    assert "Immediate recent conversation context" in llm.messages[-2].content
    assert "Latest Instagram post mentioned the July talk show" in prompt_text
    assert "Active knowledge context: none available." in llm.messages[0].content
