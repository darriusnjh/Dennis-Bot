from __future__ import annotations

import re
from typing import Any

from dennis_bot.telegram.models import (
    NormalizedTelegramUpdate,
    TelegramChat,
    TelegramCommand,
    TelegramUser,
)

_COMMAND_RE = re.compile(r"^/([A-Za-z0-9_]+)(?:@([A-Za-z0-9_]+))?(?:\s+(.*))?$", re.DOTALL)


def normalize_update(
    update: dict[str, Any],
    *,
    bot_username: str | None = None,
    bot_user_id: int | None = None,
) -> NormalizedTelegramUpdate | None:
    message = (
        update.get("message")
        or update.get("edited_message")
        or update.get("channel_post")
        or update.get("edited_channel_post")
    )
    if not message or "chat" not in message or "message_id" not in message:
        return None

    text = message.get("text") or message.get("caption")
    command = _parse_command(text, bot_username=bot_username)
    return NormalizedTelegramUpdate(
        update_id=int(update.get("update_id", 0)),
        message_id=int(message["message_id"]),
        chat=TelegramChat.from_api(message["chat"]),
        sender=TelegramUser.from_api(message.get("from")),
        text=text,
        command=command,
        reply_to_bot=_reply_to_bot(
            message,
            bot_username=bot_username,
            bot_user_id=bot_user_id,
        ),
        mentioned_bot=_mentions_bot(
            text,
            message,
            bot_username=bot_username,
            bot_user_id=bot_user_id,
        ),
        raw=update,
    )


def _parse_command(text: str | None, *, bot_username: str | None) -> TelegramCommand | None:
    if not text:
        return None
    match = _COMMAND_RE.match(text.strip())
    if not match:
        return None
    name, target_username, args = match.groups()
    normalized_bot_username = _normalize_bot_username(bot_username)
    if (
        target_username
        and normalized_bot_username
        and target_username.lower() != normalized_bot_username
    ):
        return None
    return TelegramCommand(name=name.lower(), args=args or "", bot_username=target_username)


def _reply_to_bot(
    message: dict[str, Any],
    *,
    bot_username: str | None,
    bot_user_id: int | None,
) -> bool:
    replied = message.get("reply_to_message") or {}
    sender = replied.get("from") or {}
    if bot_user_id is not None:
        return sender.get("id") == bot_user_id
    if bot_username and sender.get("username"):
        return str(sender["username"]).lower() == _normalize_bot_username(bot_username)
    return bool(sender.get("is_bot"))


def _mentions_bot(
    text: str | None,
    message: dict[str, Any],
    *,
    bot_username: str | None,
    bot_user_id: int | None,
) -> bool:
    if not text:
        return False
    normalized_bot_username = _normalize_bot_username(bot_username)
    if normalized_bot_username:
        pattern = re.compile(
            rf"(?<!\w)@{re.escape(normalized_bot_username)}(?!\w)",
            re.IGNORECASE,
        )
        if pattern.search(text):
            return True

    for entity in _message_entities(message):
        entity_type = entity.get("type")
        if entity_type == "text_mention" and bot_user_id is not None:
            user = entity.get("user") or {}
            if user.get("id") == bot_user_id:
                return True
        if entity_type == "mention" and normalized_bot_username:
            mention_text = _entity_text(text, entity)
            if mention_text.lstrip("@").lower() == normalized_bot_username:
                return True
    return False


def _message_entities(message: dict[str, Any]) -> list[dict[str, Any]]:
    entities = message.get("entities") or message.get("caption_entities") or []
    return [entity for entity in entities if isinstance(entity, dict)]


def _entity_text(text: str, entity: dict[str, Any]) -> str:
    try:
        offset = int(entity.get("offset", 0))
        length = int(entity.get("length", 0))
    except (TypeError, ValueError):
        return ""
    if offset < 0 or length < 1:
        return ""
    return text[offset : offset + length]


def _normalize_bot_username(bot_username: str | None) -> str | None:
    if not bot_username:
        return None
    normalized = bot_username.strip().lstrip("@").lower()
    return normalized or None
