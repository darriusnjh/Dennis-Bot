from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from dennis_bot.admin.commands import (
    KnowledgeCommandService,
    MemoryCommandService,
    WatchCommandService,
)
from dennis_bot.admin.policy import AdminPolicy
from dennis_bot.config import Settings
from dennis_bot.stickers.service import StickerService
from dennis_bot.telegram.models import NormalizedTelegramUpdate


class MessageTelegramClient(Protocol):
    async def send_message(
        self,
        chat_id: int | str,
        text: str,
        *,
        reply_to_message_id: int | None = None,
        parse_mode: str | None = None,
        disable_web_page_preview: bool | None = None,
    ) -> dict: ...


@dataclass
class CommandRouter:
    telegram: MessageTelegramClient
    policy: AdminPolicy
    settings: Settings
    sticker_service: StickerService | None = None
    memory_service: MemoryCommandService | None = None
    knowledge_service: KnowledgeCommandService | None = None
    watch_service: WatchCommandService | None = None

    async def handle_update(self, update: NormalizedTelegramUpdate) -> bool:
        if not update.addressed_to_bot:
            return False
        if not update.command:
            return False

        command = update.command.name
        args = update.command.args.strip()
        handler = getattr(self, f"_handle_{command}", None)
        if handler is None:
            await self._reply(update, "I do not know that command yet. Try /help.")
            return True
        await handler(update, args)
        return True

    async def _handle_start(self, update: NormalizedTelegramUpdate, _args: str) -> None:
        await self._reply(
            update,
            "Dennis Bot is online. Use /help for commands.",
        )

    async def _handle_help(self, update: NormalizedTelegramUpdate, _args: str) -> None:
        await self._reply(
            update,
            "\n".join(
                [
                    "Commands:",
                    "/start - initialize this chat",
                    "/help - show commands",
                    "/status - show bot health",
                    "/settings - show chat policy",
                    "/memory search|stats|finalize",
                    "/kb list|status|update",
                    "/stickers list|test|save|refresh",
                    "/watch list|run|pause|resume",
                ]
            ),
        )

    async def _handle_status(self, update: NormalizedTelegramUpdate, _args: str) -> None:
        issues = self.settings.validate_for_runtime(
            mode="polling" if self.settings.telegram_use_polling else "webhook"
        )
        lines = [
            "Status:",
            f"mode: {'polling' if self.settings.telegram_use_polling else 'webhook'}",
            f"config: {'ok' if not issues else f'{len(issues)} issue(s)'}",
            f"trusted_group: {'yes' if self.policy.is_trusted_group(update.chat.id) else 'no'}",
            f"full_memory_access: {'yes' if self.policy.has_full_memory_access(update) else 'no'}",
        ]
        await self._reply(update, "\n".join(lines))

    async def _handle_settings(self, update: NormalizedTelegramUpdate, _args: str) -> None:
        if not await self._require_admin(update):
            return
        await self._reply(
            update,
            "\n".join(
                [
                    "Settings:",
                    f"chat_id: {update.chat.id}",
                    f"chat_type: {update.chat.type}",
                    f"admin: {'yes' if self.policy.is_admin_update(update) else 'no'}",
                    "trusted_group: "
                    f"{'yes' if self.policy.is_trusted_group(update.chat.id) else 'no'}",
                    f"sticker_packs: {len(self.settings.telegram_sticker_packs)} configured",
                ]
            ),
        )

    async def _handle_memory(self, update: NormalizedTelegramUpdate, args: str) -> None:
        if not await self._require_admin(update):
            return
        if not self.memory_service:
            await self._reply(update, "Memory service is not configured.")
            return
        action, rest = _split_action(args)
        if action in {"", "list"}:
            await self._reply(
                update,
                await self.memory_service.list(
                    rest,
                    chat_id=update.chat.id,
                    user_id=update.sender.id if update.sender else None,
                ),
            )
        elif action in {"add", "remember"} and rest:
            await self._reply(
                update,
                await self.memory_service.add(
                    rest,
                    chat_id=update.chat.id,
                    user_id=update.sender.id if update.sender else None,
                ),
            )
        elif action == "search" and rest:
            await self._reply(update, await self.memory_service.search(rest))
        elif action in {"delete", "remove"} and rest:
            try:
                memory_id = int(rest.split()[0])
            except ValueError:
                await self._reply(update, "Usage: /memory delete <id>")
                return
            await self._reply(update, await self.memory_service.delete(memory_id))
        elif action == "stats":
            await self._reply(update, await self.memory_service.stats())
        elif action == "finalize":
            await self._reply(update, await self.memory_service.finalize(update.chat.id))
        else:
            await self._reply(
                update,
                "Usage: /memory list, /memory add <text>, /memory search <query>, "
                "/memory delete <id>, /memory stats, /memory finalize",
            )

    async def _handle_kb(self, update: NormalizedTelegramUpdate, args: str) -> None:
        if not await self._require_admin(update):
            return
        if not self.knowledge_service:
            await self._reply(update, "Knowledge service is not configured.")
            return
        action, rest = _split_action(args)
        if action == "list":
            await self._reply(update, await self.knowledge_service.list_states())
        elif action == "status":
            await self._reply(update, await self.knowledge_service.status(update.chat.id))
        elif action in {"show", "inspect"}:
            await self._reply(update, await self.knowledge_service.inspect(rest or None))
        elif action in {"switch", "use"} and rest:
            await self._reply(update, await self.knowledge_service.switch(update.chat.id, rest))
        elif action == "update":
            await self._reply(update, await self.knowledge_service.update(rest))
        else:
            await self._reply(
                update,
                "Usage: /kb list, /kb status, /kb show <name>, /kb switch <name>, "
                "/kb update <source-or-note>",
            )

    async def _handle_stickers(self, update: NormalizedTelegramUpdate, args: str) -> None:
        if not self.sticker_service:
            await self._reply(update, "Sticker service is not configured.")
            return
        action, rest = _split_action(args)
        if action in {"", "list"}:
            aliases = await self.sticker_service.list_aliases()
            if not aliases:
                await self._reply(update, "No sticker aliases are configured.")
                return
            await self._reply(
                update,
                "Sticker aliases: " + ", ".join(item.alias for item in aliases),
            )
        elif action in {"test", "send"} and rest:
            sent = await self.sticker_service.send_alias(
                update.chat.id,
                rest.split()[0],
                reply_to_message_id=update.message_id,
            )
            if not sent:
                await self._reply(update, f"Unknown sticker alias: {rest.split()[0]}")
        elif action == "save" and rest:
            if not await self._require_admin(update):
                return
            alias = rest.split()[0]
            sample = _reply_sticker(update)
            if sample is None:
                await self._reply(update, "Reply to a sticker with /stickers save <alias>.")
                return
            saved = await self.sticker_service.save_alias(
                alias,
                sample["file_id"],
                emoji=sample.get("emoji"),
                tags=(alias,),
            )
            await self._reply(update, f"Saved sticker alias: {saved.alias}")
        elif action == "refresh":
            if not await self._require_admin(update):
                return
            results = await self.sticker_service.sync_packs(self.settings.telegram_sticker_packs)
            if not results:
                await self._reply(update, "No sticker packs are configured.")
                return
            summary = ", ".join(f"{name}: {count}" for name, count in results.items())
            await self._reply(update, f"Sticker packs refreshed: {summary}")
        else:
            await self._reply(
                update,
                "Usage: /stickers list, /stickers test <alias>, /stickers save <alias>, /stickers refresh",
            )

    async def _handle_watch(self, update: NormalizedTelegramUpdate, args: str) -> None:
        if not await self._require_admin(update):
            return
        if not self.watch_service:
            await self._reply(update, "Watch service is not configured.")
            return
        action, rest = _split_action(args)
        name = rest.split()[0] if rest else ""
        if action == "list":
            await self._reply(update, await self.watch_service.list_monitors())
        elif action == "run" and name:
            await self._reply(update, await self.watch_service.run(name))
        elif action == "pause" and name:
            await self._reply(update, await self.watch_service.pause(name))
        elif action == "resume" and name:
            await self._reply(update, await self.watch_service.resume(name))
        else:
            await self._reply(update, "Usage: /watch list, /watch run|pause|resume <name>")

    async def _require_admin(self, update: NormalizedTelegramUpdate) -> bool:
        if self.policy.is_admin_update(update):
            return True
        await self._reply(update, "This command is restricted to bot admins.")
        return False

    async def _reply(self, update: NormalizedTelegramUpdate, text: str) -> None:
        await self.telegram.send_message(
            update.chat.id,
            text,
            reply_to_message_id=update.message_id,
            disable_web_page_preview=True,
        )


def _split_action(args: str) -> tuple[str, str]:
    stripped = args.strip()
    if not stripped:
        return "", ""
    parts = stripped.split(maxsplit=1)
    return parts[0].lower(), parts[1] if len(parts) == 2 else ""


def _reply_sticker(update: NormalizedTelegramUpdate) -> dict | None:
    message = (
        update.raw.get("message")
        or update.raw.get("edited_message")
        or update.raw.get("channel_post")
        or update.raw.get("edited_channel_post")
        or {}
    )
    reply = message.get("reply_to_message") or {}
    sticker = reply.get("sticker")
    if isinstance(sticker, dict) and sticker.get("file_id"):
        return sticker
    return None
