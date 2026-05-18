from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class StickerAlias:
    alias: str
    file_id: str
    pack_name: str
    emoji: str | None = None
    tags: tuple[str, ...] = ()
    enabled: bool = True


class StickerAliasStore(Protocol):
    async def upsert_pack(self, pack_name: str, title: str | None) -> None: ...

    async def upsert_alias(self, alias: StickerAlias) -> None: ...

    async def get_alias(self, alias: str) -> StickerAlias | None: ...

    async def list_aliases(self) -> list[StickerAlias]: ...


@dataclass
class InMemoryStickerAliasStore:
    packs: dict[str, str | None] = field(default_factory=dict)
    aliases: dict[str, StickerAlias] = field(default_factory=dict)

    async def upsert_pack(self, pack_name: str, title: str | None) -> None:
        self.packs[pack_name] = title

    async def upsert_alias(self, alias: StickerAlias) -> None:
        self.aliases[alias.alias.lower()] = alias

    async def get_alias(self, alias: str) -> StickerAlias | None:
        item = self.aliases.get(alias.lower())
        if item and item.enabled:
            return item
        return None

    async def list_aliases(self) -> list[StickerAlias]:
        return sorted(
            [alias for alias in self.aliases.values() if alias.enabled],
            key=lambda item: item.alias,
        )
