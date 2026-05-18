from pathlib import Path

from dennis_bot.db import Database, run_migrations
from dennis_bot.repositories import (
    ChatRepository,
    ConversationMessageRepository,
    MemoryRepository,
    MemorySessionRepository,
    MonitorRepository,
    StickerRepository,
    UserRepository,
)


async def test_conversation_memory_and_session_repositories(tmp_path: Path) -> None:
    database = Database(tmp_path / "dennis.sqlite3")

    async with database.connect() as connection:
        await run_migrations(connection)
        users = UserRepository(connection)
        chats = ChatRepository(connection)
        messages = ConversationMessageRepository(connection)
        memories = MemoryRepository(connection)
        sessions = MemorySessionRepository(connection)

        await users.upsert(123, display_name="Dennis", role="owner")
        await chats.upsert(456, chat_type="direct", title="Dennis DM")
        session = await sessions.create(
            telegram_chat_id=456,
            simplemem_tenant_id="dennis-bot-global",
            simplemem_project="dennis-bot",
            simplemem_memory_session_id="sm-session-1",
            max_message_count=30,
        )
        updated_session = await sessions.increment_message_count(session["id"], 2)

        message = await messages.add(
            telegram_chat_id=456,
            telegram_user_id=123,
            telegram_message_id=99,
            direction="inbound",
            content="Remember that my exam is on Friday.",
            simplemem_session_id="sm-session-1",
            included_in_simplemem=True,
        )
        memory = await memories.add(
            scope="user_profile",
            owner_user_id=123,
            chat_id=456,
            simplemem_tenant_id="dennis-bot-global",
            simplemem_session_id="sm-session-1",
            simplemem_entry_id="entry-1",
            content="Dennis has an exam on Friday.",
            tags='["exam"]',
            importance=0.8,
            confidence=0.9,
            source_message_id=message["id"],
        )
        listed = await memories.list(chat_id=456)
        matches = await memories.search("exam", chat_id=456)
        active_sessions = await sessions.list_active()
        await memories.soft_delete(memory["id"])
        deleted = await memories.get(memory["id"], include_deleted=True)
        matches_after_delete = await memories.search("exam", chat_id=456)

    assert updated_session["message_count"] == 2
    assert message["content_hash"]
    assert listed[0]["id"] == memory["id"]
    assert matches[0]["simplemem_entry_id"] == "entry-1"
    assert active_sessions[0]["id"] == session["id"]
    assert deleted["deleted_at"] is not None
    assert matches_after_delete == []


async def test_sticker_and_monitor_repositories(tmp_path: Path) -> None:
    database = Database(tmp_path / "dennis.sqlite3")

    async with database.connect() as connection:
        await run_migrations(connection)
        chats = ChatRepository(connection)
        stickers = StickerRepository(connection)
        monitors = MonitorRepository(connection)

        await chats.upsert(-100, chat_type="supergroup", title="Trusted Group")
        pack = await stickers.upsert_pack(pack_name="dennis_pack", title="Dennis Pack")
        await stickers.upsert_alias(
            pack_id=pack["id"],
            alias="thinking",
            file_id="sticker-file-id",
            emoji=":thinking:",
            tags='["ponder"]',
        )
        alias = await stickers.get_alias("thinking")

        monitor = await monitors.upsert_monitor(
            name="dennis_instagram",
            url="https://www.instagram.com/dennistohsg/",
            monitor_type="instagram_profile",
            provider="brightdata",
            schedule="every 60 minutes",
            change_detection_strategy="provider_activity",
            target_chat_id=-100,
            source_handle="dennistohsg",
        )
        run = await monitors.create_run(
            web_monitor_id=monitor["id"],
            provider="brightdata",
            request_mode="sync",
            status="running",
        )
        await monitors.finish_run(run["id"], status="succeeded", records_returned=1, records_changed=1)
        first_activity = await monitors.add_social_activity(
            web_monitor_id=monitor["id"],
            external_id="post-1",
            activity_type="post",
            permalink="https://instagram.com/p/post-1",
            media_type="post",
            caption="new work",
            caption_hash="hash-1",
        )
        duplicate_activity = await monitors.add_social_activity(
            web_monitor_id=monitor["id"],
            external_id="post-1",
            activity_type="post",
            caption_hash="hash-1",
        )

    assert alias is not None
    assert alias["file_id"] == "sticker-file-id"
    assert first_activity is not None
    assert duplicate_activity is None
