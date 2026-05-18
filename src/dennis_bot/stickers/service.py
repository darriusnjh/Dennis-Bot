from __future__ import annotations

from typing import Any, Protocol

from dennis_bot.stickers.store import StickerAlias, StickerAliasStore

ANY_STICKER_ALIASES = {"any", "default", "saved"}


class StickerTelegramClient(Protocol):
    async def get_sticker_set(self, name: str) -> dict[str, Any]: ...

    async def send_sticker(
        self,
        chat_id: int | str,
        sticker: str,
        *,
        reply_to_message_id: int | None = None,
    ) -> dict[str, Any]: ...


class StickerService:
    def __init__(self, telegram: StickerTelegramClient, store: StickerAliasStore) -> None:
        self.telegram = telegram
        self.store = store

    async def sync_pack(self, pack_name: str) -> int:
        sticker_set = await self.telegram.get_sticker_set(pack_name)
        await self.store.upsert_pack(pack_name, sticker_set.get("title"))
        count = 0
        for index, sticker in enumerate(sticker_set.get("stickers", []), start=1):
            file_id = sticker.get("file_id")
            if not file_id:
                continue
            alias = _alias_for_sticker(sticker, index)
            await self.store.upsert_alias(
                StickerAlias(
                    alias=alias,
                    file_id=file_id,
                    pack_name=pack_name,
                    emoji=sticker.get("emoji"),
                    tags=tuple(sticker.get("keywords") or ()),
                )
            )
            count += 1
        return count

    async def sync_packs(self, pack_names: list[str]) -> dict[str, int]:
        results: dict[str, int] = {}
        for pack_name in pack_names:
            results[pack_name] = await self.sync_pack(pack_name)
        return results

    async def save_alias(
        self,
        alias: str,
        file_id: str,
        *,
        pack_name: str = "manual_samples",
        emoji: str | None = None,
        tags: tuple[str, ...] = (),
    ) -> StickerAlias:
        sticker = StickerAlias(
            alias=_normalize_alias(alias),
            file_id=file_id,
            pack_name=pack_name,
            emoji=emoji,
            tags=tags,
        )
        await self.store.upsert_alias(sticker)
        return sticker

    async def list_aliases(self) -> list[StickerAlias]:
        return await self.store.list_aliases()

    async def resolve_alias(self, alias: str) -> StickerAlias | None:
        return await self.store.get_alias(alias)

    async def resolve_first_available_alias(self, aliases: list[str]) -> StickerAlias | None:
        normalized = [_normalize_alias(alias) for alias in aliases if _normalize_alias(alias)]
        for alias in normalized:
            sticker = await self.resolve_alias(alias)
            if sticker:
                return sticker
        wanted = set(normalized)
        if not wanted:
            return None
        allow_any = bool(wanted.intersection(ANY_STICKER_ALIASES))
        wanted.difference_update(ANY_STICKER_ALIASES)
        available = await self.list_aliases()
        for sticker in available:
            alias = _normalize_alias(sticker.alias)
            tags = {_normalize_alias(tag) for tag in sticker.tags}
            emoji = _normalize_alias(sticker.emoji or "")
            if alias in wanted or emoji in wanted or wanted.intersection(tags):
                return sticker
        if allow_any and available:
            return available[0]
        return None

    async def send_alias(
        self,
        chat_id: int | str,
        alias: str,
        *,
        reply_to_message_id: int | None = None,
    ) -> bool:
        sticker = await self.resolve_alias(alias)
        if not sticker:
            return False
        await self.telegram.send_sticker(
            chat_id,
            sticker.file_id,
            reply_to_message_id=reply_to_message_id,
        )
        return True

    async def send_first_available_alias(
        self,
        chat_id: int | str,
        aliases: list[str],
        *,
        reply_to_message_id: int | None = None,
    ) -> str | None:
        sticker = await self.resolve_first_available_alias(aliases)
        if not sticker:
            return None
        await self.telegram.send_sticker(
            chat_id,
            sticker.file_id,
            reply_to_message_id=reply_to_message_id,
        )
        return sticker.alias


def _alias_for_sticker(sticker: dict[str, Any], index: int) -> str:
    keywords = sticker.get("keywords") or []
    for keyword in keywords:
        value = _normalize_alias(str(keyword))
        if value:
            return value
    emoji = sticker.get("emoji")
    if emoji:
        return f"emoji_{index}"
    return f"sticker_{index}"


def _normalize_alias(value: str) -> str:
    return value.strip().lower().replace(" ", "_").replace("-", "_")
