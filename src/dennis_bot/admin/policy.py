from __future__ import annotations

from dataclasses import dataclass

from dennis_bot.config import Settings
from dennis_bot.telegram.models import NormalizedTelegramUpdate


@dataclass(frozen=True)
class AdminPolicy:
    admin_user_ids: frozenset[int]
    trusted_group_chat_id: int | None = None

    @classmethod
    def from_settings(cls, settings: Settings) -> AdminPolicy:
        return cls(
            admin_user_ids=frozenset(settings.admin_telegram_user_ids),
            trusted_group_chat_id=settings.trusted_group_chat_id,
        )

    def is_admin_user(self, user_id: int | None) -> bool:
        return user_id is not None and user_id in self.admin_user_ids

    def is_admin_update(self, update: NormalizedTelegramUpdate) -> bool:
        return self.is_admin_user(update.sender.id if update.sender else None)

    def is_trusted_group(self, chat_id: int) -> bool:
        return self.trusted_group_chat_id is not None and chat_id == self.trusted_group_chat_id

    def has_full_memory_access(self, update: NormalizedTelegramUpdate) -> bool:
        if update.chat.is_group:
            return self.is_trusted_group(update.chat.id)
        return self.is_admin_update(update)


class AuthorizationError(PermissionError):
    pass
