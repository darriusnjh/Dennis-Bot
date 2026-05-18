from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

ChatType = Literal["private", "group", "supergroup", "channel"]


@dataclass(frozen=True)
class TelegramUser:
    id: int
    is_bot: bool = False
    first_name: str | None = None
    username: str | None = None

    @classmethod
    def from_api(cls, data: dict[str, Any] | None) -> TelegramUser | None:
        if not data or "id" not in data:
            return None
        return cls(
            id=int(data["id"]),
            is_bot=bool(data.get("is_bot", False)),
            first_name=data.get("first_name"),
            username=data.get("username"),
        )


@dataclass(frozen=True)
class TelegramChat:
    id: int
    type: ChatType
    title: str | None = None
    username: str | None = None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> TelegramChat:
        return cls(
            id=int(data["id"]),
            type=data.get("type", "private"),
            title=data.get("title"),
            username=data.get("username"),
        )

    @property
    def is_group(self) -> bool:
        return self.type in {"group", "supergroup"}


@dataclass(frozen=True)
class TelegramCommand:
    name: str
    args: str = ""
    bot_username: str | None = None


@dataclass(frozen=True)
class NormalizedTelegramUpdate:
    update_id: int
    message_id: int
    chat: TelegramChat
    sender: TelegramUser | None
    text: str | None
    command: TelegramCommand | None
    raw: dict[str, Any]
    reply_to_bot: bool = False
    mentioned_bot: bool = False

    @property
    def is_command(self) -> bool:
        return self.command is not None

    @property
    def addressed_to_bot(self) -> bool:
        if not self.chat.is_group:
            return True
        return self.is_command or self.reply_to_bot or self.mentioned_bot
