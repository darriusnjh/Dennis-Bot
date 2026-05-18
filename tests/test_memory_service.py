from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from dennis_bot.memory.service import (
    ConversationMessageRecord,
    MemoryService,
    MemorySessionRecord,
)


class FakeSimpleMem:
    def __init__(self):
        self.started = []
        self.recorded = []
        self.finalized = []
        self.context_queries = []
        self.search_queries = []
        self.fail_record_message = False

    async def health_check(self):
        return {"ok": True}

    async def start_session(self, chat_id, metadata=None):
        session_id = f"simple-{chat_id}-{len(self.started) + 1}"
        self.started.append((chat_id, metadata))
        return {"session_id": session_id}

    async def record_message(self, session_id, role, content, metadata=None):
        if self.fail_record_message:
            raise RuntimeError("simplemem unavailable")
        self.recorded.append((session_id, role, content, metadata))
        return {"entry_id": f"entry-{len(self.recorded)}"}

    async def retrieve_context(self, query, chat_id=None, limit=8, metadata=None):
        self.context_queries.append((query, chat_id, limit, metadata))
        return {"context": ["remembered"]}

    async def search_memory(self, query, limit=10):
        self.search_queries.append((query, limit))
        return {"matches": [{"content": "result"}]}

    async def finalize_session(self, session_id, reason="message_limit"):
        self.finalized.append((session_id, reason))
        return {"report_ref": f"report-{session_id}"}

    async def stats(self):
        return {"messages": len(self.recorded)}


class FakeRepository:
    def __init__(self):
        self.sessions: dict[str, MemorySessionRecord] = {}
        self.messages: list[ConversationMessageRecord] = []
        self.next_id = 1

    async def get_active_session(self, telegram_chat_id):
        for session in self.sessions.values():
            if session.telegram_chat_id == telegram_chat_id and session.status == "active":
                return session
        return None

    async def list_active_sessions(self):
        return [session for session in self.sessions.values() if session.status == "active"]

    async def create_session(
        self,
        telegram_chat_id,
        simplemem_tenant_id,
        simplemem_project,
        simplemem_memory_session_id,
        max_message_count,
    ):
        session = MemorySessionRecord(
            id=f"local-{self.next_id}",
            telegram_chat_id=telegram_chat_id,
            simplemem_tenant_id=simplemem_tenant_id,
            simplemem_project=simplemem_project,
            simplemem_memory_session_id=simplemem_memory_session_id,
            message_count=0,
            max_message_count=max_message_count,
            status="active",
            started_at=datetime.now(UTC),
        )
        self.next_id += 1
        self.sessions[session.id] = session
        return session

    async def increment_message_count(self, session_id):
        session = self.sessions[session_id]
        session.message_count += 1
        return session.message_count

    async def mark_session_finalizing(self, session_id):
        self.sessions[session_id].status = "finalizing"

    async def mark_session_finalized(self, session_id, finalized_at, finalization_report_ref):
        session = self.sessions[session_id]
        session.status = "finalized"
        session.finalized_at = finalized_at
        session.finalization_report_ref = finalization_report_ref

    async def mark_session_failed(self, session_id, error_message):
        session = self.sessions[session_id]
        session.status = "failed"
        session.error_message = error_message

    async def record_conversation_message(self, message):
        self.messages.append(replace(message))

    async def list_recent_conversation(self, telegram_chat_id, *, limit=8):
        messages = [
            message for message in self.messages if message.telegram_chat_id == telegram_chat_id
        ]
        return messages[-limit:]


class FakeMemoryRepository:
    def __init__(self):
        self.records = []
        self.deleted = set()
        self.next_id = 1

    async def add(self, **values):
        record = {"id": self.next_id, **values, "deleted_at": None}
        self.next_id += 1
        self.records.append(record)
        return record

    async def list(self, *, scope=None, chat_id=None, owner_user_id=None, limit=50):
        records = [record for record in self.records if record["deleted_at"] is None]
        if scope is not None:
            records = [record for record in records if record["scope"] == scope]
        if chat_id is not None:
            records = [record for record in records if record["chat_id"] == chat_id]
        if owner_user_id is not None:
            records = [record for record in records if record["owner_user_id"] == owner_user_id]
        return records[:limit]

    async def search(self, query, *, chat_id=None, limit=20):
        records = [record for record in self.records if record["deleted_at"] is None]
        records = [record for record in records if query.lower() in record["content"].lower()]
        if chat_id is not None:
            records = [record for record in records if record["chat_id"] == chat_id]
        return records[:limit]

    async def soft_delete(self, memory_id):
        self.deleted.add(memory_id)
        for record in self.records:
            if record["id"] == memory_id:
                record["deleted_at"] = "now"


def service(max_session_messages=3):
    simplemem = FakeSimpleMem()
    repository = FakeRepository()
    return (
        MemoryService(
            simplemem=simplemem,
            repository=repository,
            tenant_id="tenant",
            project="project",
            max_session_messages=max_session_messages,
        ),
        simplemem,
        repository,
    )


def service_with_memory_repository(max_session_messages=3):
    simplemem = FakeSimpleMem()
    repository = FakeRepository()
    memory_repository = FakeMemoryRepository()
    return (
        MemoryService(
            simplemem=simplemem,
            repository=repository,
            tenant_id="tenant",
            project="project",
            max_session_messages=max_session_messages,
            memory_repository=memory_repository,
        ),
        simplemem,
        repository,
        memory_repository,
    )


@pytest.mark.asyncio
async def test_records_user_and_assistant_messages_to_active_session():
    memory, simplemem, repository = service()

    await memory.record_user_message(telegram_chat_id=10, content="hello", telegram_user_id=20)
    await memory.record_assistant_message(telegram_chat_id=10, content="hi")

    assert len(simplemem.started) == 1
    assert [item[1] for item in simplemem.recorded] == ["user", "assistant"]
    assert repository.messages[0].direction == "inbound"
    assert repository.messages[1].direction == "outbound"


@pytest.mark.asyncio
async def test_finalizes_at_limit_and_starts_next_session_on_following_message():
    memory, simplemem, repository = service(max_session_messages=3)

    for index in range(3):
        await memory.record_user_message(telegram_chat_id=10, content=f"message {index}")

    assert simplemem.finalized == [("simple-10-1", "message_limit")]
    finalized_sessions = [
        session for session in repository.sessions.values() if session.status == "finalized"
    ]
    assert len(finalized_sessions) == 1
    assert finalized_sessions[0].message_count == 3

    await memory.record_user_message(telegram_chat_id=10, content="message 31")

    assert len(simplemem.started) == 2
    assert simplemem.recorded[-1][0] == "simple-10-2"
    active = await repository.get_active_session(10)
    assert active is not None
    assert active.message_count == 1


@pytest.mark.asyncio
async def test_maintains_one_active_session_per_chat():
    memory, simplemem, repository = service(max_session_messages=10)

    await memory.record_user_message(telegram_chat_id=10, content="a")
    await memory.record_user_message(telegram_chat_id=20, content="b")
    await memory.record_assistant_message(telegram_chat_id=10, content="c")

    assert [start[0] for start in simplemem.started] == [10, 20]
    active_10 = await repository.get_active_session(10)
    active_20 = await repository.get_active_session(20)
    assert active_10 is not None
    assert active_20 is not None
    assert active_10.message_count == 2
    assert active_20.message_count == 1


@pytest.mark.asyncio
async def test_retrieve_search_stats_and_manual_finalize_delegate_to_simplemem():
    memory, simplemem, _repository = service(max_session_messages=10)
    await memory.record_user_message(telegram_chat_id=10, content="hello")

    context = await memory.retrieve_context(
        telegram_chat_id=10,
        query="what do you remember?",
        limit=4,
    )
    search = await memory.search("hello")
    stats = await memory.stats()
    finalized = await memory.finalize_active_session(telegram_chat_id=10)

    assert context == {"context": ["remembered"]}
    assert search == {"matches": [{"content": "result"}]}
    assert stats == {"messages": 1}
    assert finalized is not None
    assert simplemem.context_queries == [
        ("what do you remember?", 10, 4, {"full_memory_access": False})
    ]
    assert simplemem.finalized == [("simple-10-1", "manual")]


@pytest.mark.asyncio
async def test_recent_conversation_context_keeps_previous_turns_for_followups():
    memory, _simplemem, _repository = service(max_session_messages=10)

    await memory.record_user_message(
        telegram_chat_id=10,
        content="What are my latest Instagram posts?",
        telegram_user_id=20,
        telegram_message_id=100,
    )
    await memory.record_assistant_message(
        telegram_chat_id=10,
        content="The latest post mentions the July talk show 人生终点站.",
    )
    await memory.record_user_message(
        telegram_chat_id=10,
        content="can you tell me more about that event?",
        telegram_user_id=20,
        telegram_message_id=101,
    )

    context = await memory.retrieve_recent_conversation(
        telegram_chat_id=10,
        exclude_message_id=101,
    )

    assert "Recent Telegram conversation" in context
    assert "July talk show 人生终点站" in context
    assert "can you tell me more about that event" not in context


@pytest.mark.asyncio
async def test_simplemem_record_failure_still_writes_conversation_history():
    memory, simplemem, repository = service(max_session_messages=10)
    simplemem.fail_record_message = True

    session = await memory.record_user_message(
        telegram_chat_id=10,
        content="hello",
        telegram_user_id=20,
        telegram_message_id=30,
    )

    assert session.status == "failed"
    assert repository.messages[0].content == "hello"
    assert repository.messages[0].included_in_simplemem is False
    assert repository.messages[0].memory_extraction_status == "failed"
    assert repository.messages[0].metadata["memory_extraction_status"] == "failed"
    assert repository.sessions[session.id].message_count == 0


@pytest.mark.asyncio
async def test_sensitive_secret_message_is_recorded_but_skipped_from_simplemem():
    memory, simplemem, repository = service(max_session_messages=10)

    session = await memory.record_user_message(
        telegram_chat_id=10,
        content="my api key is sk_abcdefghijklmnopqrstuvwxyz123456",
    )

    assert session.status == "active"
    assert simplemem.recorded == []
    assert repository.messages[0].included_in_simplemem is False
    assert repository.messages[0].memory_extraction_status == "skipped"
    assert repository.messages[0].metadata["sensitivity"] == "secret"
    assert repository.messages[0].metadata["memory_extraction_status"] == "skipped"
    assert repository.sessions[session.id].message_count == 0


@pytest.mark.asyncio
async def test_finalize_all_active_sessions():
    memory, simplemem, repository = service(max_session_messages=10)
    await memory.record_user_message(telegram_chat_id=10, content="a")
    await memory.record_user_message(telegram_chat_id=20, content="b")

    finalized = await memory.finalize_all_active_sessions()

    assert {session.telegram_chat_id for session in finalized} == {10, 20}
    assert simplemem.finalized == [
        ("simple-10-1", "shutdown"),
        ("simple-20-2", "shutdown"),
    ]
    assert all(session.status == "finalized" for session in repository.sessions.values())


@pytest.mark.asyncio
async def test_retrieve_context_propagates_full_memory_access_metadata():
    memory, simplemem, _repository = service(max_session_messages=10)

    await memory.retrieve_context(
        chat_id=10,
        user_id=20,
        query="group question",
        full_memory_access=True,
        metadata={"source": "trusted_group"},
    )

    assert simplemem.context_queries == [
        (
            "group question",
            10,
            8,
            {
                "source": "trusted_group",
                "full_memory_access": True,
                "telegram_user_id": 20,
            },
        )
    ]


@pytest.mark.asyncio
async def test_repository_backed_memory_add_list_search_and_delete():
    memory, _simplemem, _repository, memory_repository = service_with_memory_repository()

    added = await memory.add_memory(
        content="Dennis prefers concise summaries.",
        scope="user_profile",
        owner_user_id=20,
        chat_id=10,
        tags=["preference"],
    )
    secret = await memory.add_memory(
        content="Provider token is abcdefghijklmnopqrstuvwxyz123456",
        scope="project_memory",
        chat_id=10,
    )
    listed = await memory.list_memories(chat_id=10)
    matches = await memory.search_memories("concise", chat_id=10)
    await memory.delete_memory(added["id"])
    after_delete = await memory.search_memories("concise", chat_id=10)

    assert listed == [added, secret]
    assert matches == [added]
    assert secret["sensitivity"] == "secret"
    assert '"secret"' in secret["tags"]
    assert after_delete == []
    assert memory_repository.deleted == {added["id"]}
