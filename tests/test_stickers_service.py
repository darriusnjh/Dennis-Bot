from __future__ import annotations

import pytest

from dennis_bot.stickers.service import StickerService
from dennis_bot.stickers.store import InMemoryStickerAliasStore, StickerAlias


class FakeTelegram:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str, int | None]] = []

    async def get_sticker_set(self, name: str) -> dict:
        return {
            "name": name,
            "title": "Dennis Pack",
            "stickers": [
                {"file_id": "file-a", "emoji": "approved", "keywords": ["approved"]},
                {"file_id": "file-b", "emoji": "celebrate", "keywords": ["celebrate"]},
            ],
        }

    async def send_sticker(
        self,
        chat_id: int,
        sticker: str,
        *,
        reply_to_message_id: int | None = None,
    ) -> dict:
        self.sent.append((chat_id, sticker, reply_to_message_id))
        return {"message_id": 1}


@pytest.mark.asyncio
async def test_sync_pack_stores_aliases_and_send_alias() -> None:
    telegram = FakeTelegram()
    service = StickerService(telegram, InMemoryStickerAliasStore())

    count = await service.sync_pack("dennis_pack")
    sent = await service.send_alias(123, "approved", reply_to_message_id=99)

    assert count == 2
    assert sent
    assert telegram.sent == [(123, "file-a", 99)]


@pytest.mark.asyncio
async def test_send_alias_returns_false_for_missing_alias() -> None:
    telegram = FakeTelegram()
    service = StickerService(telegram, InMemoryStickerAliasStore())

    assert not await service.send_alias(123, "missing")
    assert telegram.sent == []


@pytest.mark.asyncio
async def test_send_first_available_alias_uses_alias_or_tags() -> None:
    telegram = FakeTelegram()
    store = InMemoryStickerAliasStore()
    await store.upsert_alias(
        StickerAlias(
            alias="big_smile",
            file_id="file-smile",
            pack_name="dennis_pack",
            tags=("celebrate", "approved"),
        )
    )
    service = StickerService(telegram, store)

    sent = await service.send_first_available_alias(
        123,
        ["missing", "celebrate"],
        reply_to_message_id=99,
    )

    assert sent == "big_smile"
    assert telegram.sent == [(123, "file-smile", 99)]


@pytest.mark.asyncio
async def test_send_first_available_alias_can_fallback_to_any_saved_sticker() -> None:
    telegram = FakeTelegram()
    store = InMemoryStickerAliasStore()
    await store.upsert_alias(
        StickerAlias(alias="custom_mood", file_id="file-custom", pack_name="dennis_pack")
    )
    service = StickerService(telegram, store)

    sent = await service.send_first_available_alias(
        123,
        ["missing", "any"],
        reply_to_message_id=99,
    )

    assert sent == "custom_mood"
    assert telegram.sent == [(123, "file-custom", 99)]
