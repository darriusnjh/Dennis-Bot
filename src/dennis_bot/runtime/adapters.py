from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

import aiosqlite

from dennis_bot.agents.knowledge_update.agent import KnowledgeUpdateAgent, SourceChange
from dennis_bot.knowledge.service import KnowledgeService
from dennis_bot.memory.service import (
    ConversationMessageRecord,
    MemoryService,
    MemorySessionRecord,
)
from dennis_bot.monitors.models import (
    MonitorChange,
    MonitorRunRecord,
    MonitorState,
    SocialActivityItemRecord,
)
from dennis_bot.monitors.service import MonitorService
from dennis_bot.orchestrator.service import ConversationOrchestrator, IncomingMessage
from dennis_bot.repositories.core import (
    ChatRepository,
    ConversationMessageRepository,
    MemorySessionRepository,
    UserRepository,
)
from dennis_bot.stickers.store import StickerAlias
from dennis_bot.stickers.service import StickerService
from dennis_bot.telegram.client import TelegramClient
from dennis_bot.telegram.models import NormalizedTelegramUpdate

logger = logging.getLogger(__name__)
STICKER_DIRECTIVE_RE = re.compile(r"^\s*\"?\[sticker:\s*([a-zA-Z0-9_-]+)\s*\]\"?\s*$")
STICKER_STAGE_DIRECTION_RE = re.compile(
    r"^\s*[*_({\[]?\s*(?:sends?|sending|sent|uses?|using)?\s*"
    r"(?:a\s+)?(?:(?P<alias>[a-zA-Z0-9_-]+)\s+)?sticker\s*[*)}\]_.!]*\s*$",
    re.IGNORECASE,
)
TRAILING_STICKER_STAGE_DIRECTION_RE = re.compile(
    r"\s+[*_({\[]?\s*(?:sends?|sending|sent|uses?|using)\s*"
    r"(?:a\s+)?(?:(?P<alias>[a-zA-Z0-9_-]+)\s+)?sticker\s*[*)}\]_.!]*\s*$",
    re.IGNORECASE,
)
STICKER_ALIAS_FALLBACKS = {
    "approved": (
        "approved",
        "agree",
        "chill",
        "ok",
        "okay",
        "yes",
        "steady",
        "thumbs_up",
        "like",
        "any",
    ),
    "celebrate": (
        "celebrate",
        "happy",
        "yay",
        "nice",
        "success",
        "approved",
        "agree",
        "love",
        "any",
    ),
    "encourage": (
        "encourage",
        "support",
        "chill",
        "love",
        "steady",
        "fighting",
        "jiayou",
        "jia_you",
        "agree",
        "any",
    ),
    "thinking": ("thinking", "hmm", "ponder", "confused", "chill", "agree", "any"),
    "confused": ("confused", "thinking", "hmm", "blur", "question", "chill", "any"),
    "angry": ("angry", "warning", "confused", "chill", "any"),
    "warning": ("warning", "careful", "alert"),
}


class TelegramMetadataRecorder:
    def __init__(
        self,
        connection: aiosqlite.Connection,
        *,
        trusted_group_chat_id: int | None = None,
    ) -> None:
        self.connection = connection
        self.users = UserRepository(connection)
        self.chats = ChatRepository(connection)
        self.trusted_group_chat_id = trusted_group_chat_id

    async def record_metadata(self, update: NormalizedTelegramUpdate) -> None:
        if update.sender is not None:
            await self.users.upsert(
                update.sender.id,
                display_name=update.sender.first_name or update.sender.username,
            )
        existing = await self.chats.get(update.chat.id)
        existing_full_access = bool(existing and existing["full_memory_access_enabled"])
        trusted_group = (
            self.trusted_group_chat_id is not None
            and update.chat.id == self.trusted_group_chat_id
        )
        await self.chats.upsert(
            update.chat.id,
            chat_type=_db_chat_type(update.chat.type),
            title=update.chat.title or update.chat.username,
            full_memory_access_enabled=existing_full_access or trusted_group,
        )
        await self.connection.commit()

    async def claim_update(self, update: NormalizedTelegramUpdate) -> bool:
        cursor = await self.connection.execute(
            """
            INSERT OR IGNORE INTO processed_telegram_updates (
                update_id, telegram_chat_id, telegram_message_id
            )
            VALUES (?, ?, ?)
            """,
            (update.update_id, update.chat.id, update.message_id),
        )
        await self.connection.commit()
        return cursor.rowcount == 1


class SQLiteMemorySessionRepository:
    def __init__(self, connection: aiosqlite.Connection) -> None:
        self.connection = connection
        self.sessions = MemorySessionRepository(connection)
        self.messages = ConversationMessageRepository(connection)

    async def get_active_session(self, telegram_chat_id: int) -> MemorySessionRecord | None:
        row = await self.sessions.get_active_for_chat(telegram_chat_id)
        return _memory_session_from_row(row) if row else None

    async def create_session(
        self,
        telegram_chat_id: int,
        simplemem_tenant_id: str,
        simplemem_project: str,
        simplemem_memory_session_id: str,
        max_message_count: int,
    ) -> MemorySessionRecord:
        await self._ensure_chat(telegram_chat_id)
        row = await self.sessions.create(
            telegram_chat_id=telegram_chat_id,
            simplemem_tenant_id=simplemem_tenant_id,
            simplemem_project=simplemem_project,
            simplemem_memory_session_id=simplemem_memory_session_id,
            max_message_count=max_message_count,
        )
        await self.connection.commit()
        return _memory_session_from_row(row)

    async def increment_message_count(self, session_id: str) -> int:
        row = await self.sessions.increment_message_count(int(session_id))
        await self.connection.commit()
        return int(row["message_count"])

    async def mark_session_finalizing(self, session_id: str) -> None:
        await self.sessions.mark_finalizing(int(session_id))
        await self.connection.commit()

    async def mark_session_finalized(
        self,
        session_id: str,
        finalized_at: datetime,
        finalization_report_ref: str | None,
    ) -> None:
        del finalized_at
        await self.sessions.finalize(
            int(session_id),
            finalization_report_ref=finalization_report_ref,
        )
        await self.connection.commit()

    async def mark_session_failed(self, session_id: str, error_message: str) -> None:
        await self.sessions.fail(int(session_id), error_message)
        await self.connection.commit()

    async def record_conversation_message(self, message: ConversationMessageRecord) -> None:
        await self._ensure_chat(message.telegram_chat_id)
        try:
            await self.messages.add(
                telegram_chat_id=message.telegram_chat_id,
                direction=message.direction,
                content=message.content,
                telegram_user_id=message.telegram_user_id,
                telegram_message_id=message.telegram_message_id,
                message_type=message.message_type,
                simplemem_session_id=message.simplemem_session_id,
                included_in_simplemem=message.included_in_simplemem,
                memory_extraction_status=message.memory_extraction_status,
            )
        except aiosqlite.IntegrityError:
            # Telegram may retry webhooks. Keep idempotency for already-recorded messages.
            pass
        await self.connection.commit()

    async def list_recent_conversation(
        self,
        telegram_chat_id: int,
        *,
        limit: int = 8,
    ) -> list[ConversationMessageRecord]:
        rows = await self.messages.list_for_chat(telegram_chat_id, limit=limit)
        return [_conversation_message_from_row(row) for row in reversed(rows)]

    async def list_active_sessions(self) -> list[MemorySessionRecord]:
        rows = await self.sessions.list_active()
        return [_memory_session_from_row(row) for row in rows]

    async def _ensure_chat(self, telegram_chat_id: int) -> None:
        await self.connection.execute(
            """
            INSERT OR IGNORE INTO chats (telegram_chat_id, chat_type)
            VALUES (?, 'direct')
            """,
            (telegram_chat_id,),
        )


class MemoryCommandAdapter:
    def __init__(self, memory_service: MemoryService | None) -> None:
        self.memory_service = memory_service

    async def list(
        self,
        args: str = "",
        *,
        chat_id: int,
        user_id: int | None = None,
    ) -> str:
        del user_id
        if self.memory_service is None:
            return "Memory service is not configured."
        scope = args.strip() or None
        try:
            rows = await self.memory_service.list_memories(
                scope=scope,
                chat_id=chat_id if scope in {"group_memory", "chat_session"} else None,
                limit=10,
            )
        except RuntimeError as exc:
            return str(exc)
        return _format_memory_rows("Memory records", rows)

    async def add(
        self,
        content: str,
        *,
        chat_id: int,
        user_id: int | None = None,
    ) -> str:
        if self.memory_service is None:
            return "Memory service is not configured."
        if not content.strip():
            return "Usage: /memory add <text>"
        try:
            row = await self.memory_service.add_memory(
                content=content.strip(),
                scope="user_profile",
                owner_user_id=user_id,
                chat_id=chat_id,
                confidence=1.0,
                importance=0.6,
            )
        except RuntimeError as exc:
            return str(exc)
        row_id = row.get("id") if isinstance(row, dict) else getattr(row, "id", None)
        return f"Added memory{f' #{row_id}' if row_id else ''}."

    async def search(self, query: str) -> str:
        if self.memory_service is None:
            return "Memory service is not configured."
        result = await self.memory_service.search(query)
        parts = [_format_mapping("SimpleMem search", result)]
        try:
            rows = await self.memory_service.search_memories(query, limit=10)
        except RuntimeError:
            rows = []
        if rows:
            parts.append(_format_memory_rows("Dennis Bot DB matches", rows))
        return "\n\n".join(parts)

    async def delete(self, memory_id: int) -> str:
        if self.memory_service is None:
            return "Memory service is not configured."
        try:
            await self.memory_service.delete_memory(memory_id)
        except RuntimeError as exc:
            return str(exc)
        return f"Deleted memory #{memory_id} from Dennis Bot records."

    async def stats(self) -> str:
        if self.memory_service is None:
            return "Memory service is not configured."
        result = await self.memory_service.stats()
        return _format_mapping("Memory stats", result)

    async def finalize(self, chat_id: int) -> str:
        if self.memory_service is None:
            return "Memory service is not configured."
        session = await self.memory_service.finalize_active_session(chat_id)
        if session is None:
            return "No active memory session for this chat."
        return f"Finalized memory session {session.simplemem_memory_session_id}."


class KnowledgeCommandAdapter:
    def __init__(
        self,
        knowledge_service: KnowledgeService,
        knowledge_agent: KnowledgeUpdateAgent | None = None,
    ) -> None:
        self.knowledge_service = knowledge_service
        self.knowledge_agent = knowledge_agent

    async def list_states(self) -> str:
        await self.knowledge_service.ensure_default_state()
        states = await self.knowledge_service.list_states()
        if not states:
            return "No knowledge states are configured."
        return "\n".join(
            f"{state.name} v{state.version} ({'enabled' if state.enabled else 'disabled'})"
            for state in sorted(states, key=lambda item: (item.name, -item.version))
        )

    async def status(self, chat_id: int) -> str:
        status = await self.knowledge_service.status(chat_id=chat_id)
        state_data = status.get("active_state") or {}
        sources = state_data.get("source_refs") or []
        return (
            "Knowledge status:\n"
            f"active: {state_data.get('name', 'none')} v{state_data.get('version', 'n/a')}\n"
            f"states: {status.get('state_count', 0)}\n"
            f"sources: {', '.join(sources) or 'none'}"
        )

    async def inspect(self, name: str | None = None) -> str:
        state = await self.knowledge_service.inspect_state(name)
        if state is None:
            return f"Unknown knowledge state: {name}"
        return "\n".join(
            [
                f"Knowledge state: {state.name} v{state.version}",
                f"description: {state.description}",
                f"scope: {state.access_scope}",
                f"enabled: {'yes' if state.enabled else 'no'}",
                f"sources: {', '.join(state.source_refs) or 'none'}",
            ]
        )

    async def switch(self, chat_id: int, state_name: str) -> str:
        try:
            state = await self.knowledge_service.switch_active_state(
                chat_id=chat_id,
                state_name=state_name.strip(),
            )
        except KeyError:
            return f"Unknown knowledge state: {state_name}"
        except RuntimeError as exc:
            return str(exc)
        return f"Active knowledge state is now {state.name} v{state.version}."

    async def update(self, args: str) -> str:
        if self.knowledge_agent is None:
            return "Knowledge update agent is not configured."
        text = args.strip()
        if not text:
            return "Usage: /kb update <source-or-note>"
        source_url = text.split(maxsplit=1)[0]
        decision = await self.knowledge_agent.request_manual_update(
            SourceChange(
                source_url=source_url,
                source_type="manual",
                title=f"Manual knowledge update: {source_url}",
                after=text,
                diff=text,
                detected_at=datetime.now(UTC).isoformat(),
            )
        )
        version_note = (
            f" v{decision.previous_version} -> v{decision.new_version}"
            if decision.new_version
            else f" v{decision.previous_version}"
        )
        return (
            f"Knowledge update {decision.status}: {decision.classification}"
            f"{version_note}. {decision.summary}"
        )


class WatchCommandAdapter:
    def __init__(self, monitor_service: MonitorService | None) -> None:
        self.monitor_service = monitor_service

    async def list_monitors(self) -> str:
        if self.monitor_service is None:
            return "Watch service is not configured."
        if not self.monitor_service.monitors:
            return "No monitors configured."
        lines: list[str] = []
        for monitor in self.monitor_service.monitors.values():
            enabled = await self.monitor_service.is_monitor_enabled(monitor.name)
            lines.append(f"{monitor.name}: {monitor.url} ({'enabled' if enabled else 'paused'})")
        return "\n".join(lines)

    async def run(self, name: str) -> str:
        if self.monitor_service is None:
            return "Watch service is not configured."
        try:
            changes = await self.monitor_service.run_manual(name)
        except KeyError:
            return f"Unknown monitor: {name}"
        return f"{name}: {len(changes)} change(s) detected."

    async def pause(self, name: str) -> str:
        if self.monitor_service is None:
            return "Watch service is not configured."
        try:
            await self.monitor_service.pause_monitor(name)
        except KeyError:
            return f"Unknown monitor: {name}"
        return f"Paused monitor: {name}"

    async def resume(self, name: str) -> str:
        if self.monitor_service is None:
            return "Watch service is not configured."
        try:
            await self.monitor_service.resume_monitor(name)
        except KeyError:
            return f"Unknown monitor: {name}"
        return f"Resumed monitor: {name}"

    def _get_monitor(self, name: str):
        if self.monitor_service is None:
            return None
        return self.monitor_service.monitors.get(name)


class SQLiteStickerAliasStore:
    def __init__(self, connection: aiosqlite.Connection) -> None:
        self.connection = connection

    async def upsert_pack(self, pack_name: str, title: str | None) -> None:
        await self.connection.execute(
            """
            INSERT INTO sticker_packs (pack_name, title, enabled, last_synced_at)
            VALUES (?, ?, 1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            ON CONFLICT(pack_name) DO UPDATE SET
                title = excluded.title,
                enabled = 1,
                last_synced_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            """,
            (pack_name, title),
        )
        await self.connection.commit()

    async def upsert_alias(self, alias: StickerAlias) -> None:
        await self.upsert_pack(alias.pack_name, None)
        pack_id = await self._pack_id(alias.pack_name)
        await self.connection.execute(
            """
            INSERT INTO sticker_aliases (pack_id, alias, file_id, emoji, tags, enabled)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(pack_id, alias) DO UPDATE SET
                file_id = excluded.file_id,
                emoji = excluded.emoji,
                tags = excluded.tags,
                enabled = excluded.enabled,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            """,
            (
                pack_id,
                alias.alias.lower(),
                alias.file_id,
                alias.emoji,
                ",".join(alias.tags),
                int(alias.enabled),
            ),
        )
        await self.connection.commit()

    async def get_alias(self, alias: str) -> StickerAlias | None:
        cursor = await self.connection.execute(
            """
            SELECT sticker_aliases.*, sticker_packs.pack_name
            FROM sticker_aliases
            JOIN sticker_packs ON sticker_packs.id = sticker_aliases.pack_id
            WHERE sticker_aliases.alias = ?
              AND sticker_aliases.enabled = 1
              AND sticker_packs.enabled = 1
            ORDER BY sticker_aliases.updated_at DESC
            LIMIT 1
            """,
            (alias.lower(),),
        )
        try:
            row = await cursor.fetchone()
        finally:
            await cursor.close()
        return _sticker_alias_from_row(row) if row else None

    async def list_aliases(self) -> list[StickerAlias]:
        cursor = await self.connection.execute(
            """
            SELECT sticker_aliases.*, sticker_packs.pack_name
            FROM sticker_aliases
            JOIN sticker_packs ON sticker_packs.id = sticker_aliases.pack_id
            WHERE sticker_aliases.enabled = 1
              AND sticker_packs.enabled = 1
            ORDER BY sticker_aliases.alias
            """
        )
        try:
            rows = await cursor.fetchall()
        finally:
            await cursor.close()
        return [_sticker_alias_from_row(row) for row in rows]

    async def _pack_id(self, pack_name: str) -> int:
        cursor = await self.connection.execute(
            "SELECT id FROM sticker_packs WHERE pack_name = ?",
            (pack_name,),
        )
        try:
            row = await cursor.fetchone()
        finally:
            await cursor.close()
        if row is None:
            raise ValueError(f"Sticker pack was not created: {pack_name}")
        return int(row["id"])


class TelegramNotificationService:
    def __init__(self, telegram: TelegramClient) -> None:
        self.telegram = telegram

    async def send_monitor_notification(
        self,
        *,
        chat_id: int | None,
        change: MonitorChange,
        message: str,
    ) -> None:
        del change
        if chat_id is None:
            return
        await self.telegram.send_message(chat_id, message, disable_web_page_preview=False)


class KnowledgeUpdateMonitorAdapter:
    def __init__(self, agent: KnowledgeUpdateAgent) -> None:
        self.agent = agent

    async def handle_monitor_change(self, change: MonitorChange) -> None:
        await self.agent.review_change(
            SourceChange(
                source_url=change.record.permalink or change.record.url,
                source_type=change.record.source_type,
                title=change.record.title,
                after=change.record.content,
                diff=change.record.summary,
                detected_at=change.detected_at.isoformat(),
            )
        )


class SQLiteMonitorRepository:
    def __init__(self, connection: aiosqlite.Connection) -> None:
        self.connection = connection

    async def get_monitor_state(self, monitor_name: str) -> MonitorState | None:
        row = await self._get_monitor_row(monitor_name)
        if not row:
            return None
        return MonitorState(
            monitor_name=monitor_name,
            last_seen_external_id=row["last_seen_external_id"],
            last_seen_permalink=row["last_seen_permalink"],
            last_seen_hash=row["last_seen_hash"],
            last_seen_content=row["last_seen_content_ref"],
            last_seen_published_at=_parse_datetime(row["last_seen_published_at"]),
            last_checked_at=_parse_datetime(row["last_checked_at"]),
            last_notified_at=_parse_datetime(row["last_notified_at"]),
        )

    async def upsert_monitor_state(self, state: MonitorState) -> None:
        await self.connection.execute(
            """
            UPDATE web_monitors
            SET last_seen_external_id = ?,
                last_seen_permalink = ?,
                last_seen_hash = ?,
                last_seen_content_ref = ?,
                last_seen_published_at = ?,
                last_checked_at = ?,
                last_notified_at = ?,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE name = ?
            """,
            (
                state.last_seen_external_id,
                state.last_seen_permalink,
                state.last_seen_hash,
                state.last_seen_content,
                _format_datetime(state.last_seen_published_at),
                _format_datetime(state.last_checked_at),
                _format_datetime(state.last_notified_at),
                state.monitor_name,
            ),
        )
        await self.connection.commit()

    async def has_seen_change_hash(self, monitor_name: str, content_hash: str) -> bool:
        cursor = await self.connection.execute(
            """
            SELECT 1 FROM monitor_change_hashes
            WHERE monitor_name = ? AND content_hash = ?
            """,
            (monitor_name, content_hash),
        )
        try:
            return await cursor.fetchone() is not None
        finally:
            await cursor.close()

    async def record_change_hash(self, monitor_name: str, content_hash: str) -> None:
        await self.connection.execute(
            """
            INSERT OR IGNORE INTO monitor_change_hashes (monitor_name, content_hash)
            VALUES (?, ?)
            """,
            (monitor_name, content_hash),
        )
        await self.connection.commit()

    async def create_monitor_run(self, run: MonitorRunRecord) -> MonitorRunRecord:
        monitor_id = await self._ensure_monitor_id(run.web_monitor_id)
        cursor = await self.connection.execute(
            """
            INSERT INTO monitor_runs (
                web_monitor_id, provider, provider_endpoint, provider_request_id,
                provider_snapshot_id, request_mode, status, records_returned,
                records_changed, started_at, completed_at, error_code, error_message
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                monitor_id,
                run.provider,
                run.provider_endpoint,
                run.provider_request_id,
                run.provider_snapshot_id,
                run.request_mode,
                run.status,
                run.records_returned,
                run.records_changed,
                _format_datetime(run.started_at),
                _format_datetime(run.completed_at),
                run.error_code,
                run.error_message,
            ),
        )
        await self.connection.commit()
        run.id = str(cursor.lastrowid)
        return run

    async def create_social_activity_item(
        self, item: SocialActivityItemRecord
    ) -> SocialActivityItemRecord:
        monitor_id = await self._ensure_monitor_id(item.web_monitor_id)
        cursor = await self.connection.execute(
            """
            INSERT OR IGNORE INTO social_activity_items (
                web_monitor_id, platform, external_id, activity_type, actor_handle,
                permalink, media_type, caption, caption_hash, published_at,
                thumbnail_ref, engagement_snapshot, raw_provider_record_ref, notified_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                monitor_id,
                item.platform,
                item.external_id,
                item.activity_type,
                item.actor_handle,
                item.permalink,
                item.media_type,
                item.caption,
                item.caption_hash or "",
                _format_datetime(item.published_at),
                item.thumbnail_ref,
                json.dumps(item.engagement_snapshot, sort_keys=True),
                json.dumps(item.raw_provider_record_ref, sort_keys=True),
                _format_datetime(item.notified_at),
            ),
        )
        await self.connection.commit()
        if cursor.lastrowid:
            item.id = str(cursor.lastrowid)
        return item

    async def set_monitor_enabled(self, monitor_name: str, enabled: bool) -> None:
        await self.connection.execute(
            """
            UPDATE web_monitors
            SET enabled = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE name = ?
            """,
            (int(enabled), monitor_name),
        )
        await self.connection.commit()

    async def is_monitor_enabled(self, monitor_name: str) -> bool | None:
        row = await self._get_monitor_row(monitor_name)
        if row is None:
            return None
        return bool(row["enabled"])

    async def ensure_monitor_definition(self, monitor: Any) -> None:
        if monitor.target_chat_id is not None:
            await self.connection.execute(
                """
                INSERT OR IGNORE INTO chats (telegram_chat_id, chat_type, full_memory_access_enabled)
                VALUES (?, 'supergroup', 1)
                """,
                (monitor.target_chat_id,),
            )
            await self.connection.execute(
                """
                UPDATE chats
                SET full_memory_access_enabled = 1,
                    updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE telegram_chat_id = ?
                """,
                (monitor.target_chat_id,),
            )
        await self.connection.execute(
            """
            INSERT INTO web_monitors (
                name, url, monitor_type, provider, schedule, change_detection_strategy,
                relevance_filter, impact_policy, notify_on_any_change, knowledge_update_enabled,
                target_chat_id, source_handle, enabled
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                url = excluded.url,
                monitor_type = excluded.monitor_type,
                provider = excluded.provider,
                schedule = excluded.schedule,
                change_detection_strategy = excluded.change_detection_strategy,
                relevance_filter = excluded.relevance_filter,
                impact_policy = excluded.impact_policy,
                notify_on_any_change = excluded.notify_on_any_change,
                knowledge_update_enabled = excluded.knowledge_update_enabled,
                target_chat_id = excluded.target_chat_id,
                source_handle = excluded.source_handle,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            """,
            (
                monitor.name,
                monitor.url,
                monitor.monitor_type,
                monitor.provider,
                monitor.schedule,
                monitor.change_detection_strategy,
                monitor.relevance_filter,
                monitor.impact_policy,
                int(monitor.notify_on_any_change),
                int(monitor.knowledge_update_enabled),
                monitor.target_chat_id,
                monitor.source_handle,
                int(monitor.enabled),
            ),
        )
        await self.connection.commit()

    async def _ensure_monitor_id(self, monitor_name: str) -> int:
        row = await self._get_monitor_row(monitor_name)
        if row is None:
            await self.connection.execute(
                """
                INSERT INTO web_monitors (name, url, monitor_type, provider, schedule, change_detection_strategy)
                VALUES (?, ?, 'custom', 'brightdata', 'interval:hours=1', 'normalized_content_hash')
                """,
                (monitor_name, monitor_name),
            )
            await self.connection.commit()
            row = await self._get_monitor_row(monitor_name)
        assert row is not None
        return int(row["id"])

    async def _get_monitor_row(self, monitor_name: str):
        cursor = await self.connection.execute(
            "SELECT * FROM web_monitors WHERE name = ?",
            (monitor_name,),
        )
        try:
            return await cursor.fetchone()
        finally:
            await cursor.close()


class NaturalMessageHandler:
    def __init__(
        self,
        *,
        telegram: TelegramClient,
        orchestrator: ConversationOrchestrator | None,
        policy: Any | None = None,
        knowledge_service: KnowledgeService | None = None,
        sticker_service: StickerService | None = None,
    ) -> None:
        self.telegram = telegram
        self.orchestrator = orchestrator
        self.policy = policy
        self.knowledge_service = knowledge_service
        self.sticker_service = sticker_service

    async def handle_update(self, update: NormalizedTelegramUpdate) -> bool:
        if not update.addressed_to_bot or not update.text or update.command:
            return False
        if self.orchestrator is None:
            await self.telegram.send_message(
                update.chat.id,
                "Conversation service is not configured. Check OPENAI_API_KEY and SimpleMem settings.",
                reply_to_message_id=update.message_id,
            )
            return True
        full_memory_access = (
            bool(self.policy.has_full_memory_access(update))
            if self.policy is not None
            else False
        )
        active_state_name = None
        if self.knowledge_service is not None:
            state = await self.knowledge_service.resolve_active_state(chat_id=update.chat.id)
            active_state_name = state.name if state else None
        available_sticker_moods = await self._available_sticker_moods()
        response = await self.orchestrator.respond(
            IncomingMessage(
                text=update.text,
                chat_id=update.chat.id,
                user_id=update.sender.id if update.sender else None,
                message_id=update.message_id,
                chat_type=update.chat.type,
                chat_title=update.chat.title,
                username=update.sender.username if update.sender else None,
                is_trusted_group=full_memory_access,
                active_knowledge_state=active_state_name,
                metadata={"available_sticker_moods": available_sticker_moods},
            )
        )
        response_text, explicit_sticker = _split_sticker_directive(response.text)
        sent_sticker = False
        if response_text:
            await self.telegram.send_message(
                update.chat.id,
                response_text,
                reply_to_message_id=update.message_id,
                disable_web_page_preview=True,
            )
        if explicit_sticker:
            sent_sticker = await self._send_named_sticker(update, explicit_sticker, response_text)
        if not response_text and not sent_sticker:
            await self.telegram.send_message(
                update.chat.id,
                "Can.",
                reply_to_message_id=update.message_id,
                disable_web_page_preview=True,
            )
        return True

    async def _available_sticker_moods(self) -> list[str]:
        if self.sticker_service is None:
            return []
        try:
            aliases = await self.sticker_service.list_aliases()
        except Exception:
            logger.exception("Failed to list sticker moods")
            return []
        moods: list[str] = []
        seen: set[str] = set()
        for item in aliases:
            mood = item.alias.strip().lower().replace(" ", "_").replace("-", "_")
            if not mood or mood in seen:
                continue
            seen.add(mood)
            moods.append(mood)
        return moods[:50]

    async def _send_named_sticker(
        self,
        update: NormalizedTelegramUpdate,
        alias: str,
        response_text: str,
    ) -> bool:
        if self.sticker_service is None:
            return False
        resolved_alias = _resolve_sticker_alias_for_context(
            alias,
            user_text=update.text or "",
            response_text=response_text,
        )
        try:
            sent = await self.sticker_service.send_first_available_alias(
                update.chat.id,
                _sticker_alias_candidates(resolved_alias, allow_any=False),
                reply_to_message_id=update.message_id,
            )
        except Exception:
            logger.exception("Failed to send explicit sticker")
            return False
        return sent is not None

def _memory_session_from_row(row: dict[str, Any]) -> MemorySessionRecord:
    return MemorySessionRecord(
        id=str(row["id"]),
        telegram_chat_id=int(row["telegram_chat_id"]),
        simplemem_tenant_id=row["simplemem_tenant_id"],
        simplemem_project=row["simplemem_project"],
        simplemem_memory_session_id=row["simplemem_memory_session_id"] or "",
        message_count=int(row["message_count"]),
        max_message_count=int(row["max_message_count"]),
        status=row["status"],
        started_at=_parse_datetime(row["started_at"]) or datetime.now(UTC),
        finalized_at=_parse_datetime(row["finalized_at"]),
        finalization_report_ref=row["finalization_report_ref"],
        error_message=row["error_message"],
    )


def _conversation_message_from_row(row: dict[str, Any]) -> ConversationMessageRecord:
    return ConversationMessageRecord(
        telegram_chat_id=int(row["telegram_chat_id"]),
        direction=row["direction"],
        content=row["content"] or "",
        simplemem_session_id=row["simplemem_session_id"],
        telegram_user_id=row["telegram_user_id"],
        telegram_message_id=row["telegram_message_id"],
        message_type=row["message_type"],
        included_in_simplemem=bool(row["included_in_simplemem"]),
        memory_extraction_status=row["memory_extraction_status"],
    )


def _sticker_alias_from_row(row: Any) -> StickerAlias:
    tags = tuple(tag for tag in (row["tags"] or "").split(",") if tag)
    return StickerAlias(
        alias=row["alias"],
        file_id=row["file_id"],
        pack_name=row["pack_name"],
        emoji=row["emoji"],
        tags=tags,
        enabled=bool(row["enabled"]),
    )


def _db_chat_type(telegram_chat_type: str) -> str:
    return "direct" if telegram_chat_type == "private" else telegram_chat_type


def _format_mapping(title: str, value: dict[str, Any]) -> str:
    if not value:
        return f"{title}: no data."
    return title + ":\n" + json.dumps(value, indent=2, sort_keys=True, default=str)[:3500]


def _format_memory_rows(title: str, rows: list[Any]) -> str:
    if not rows:
        return f"{title}: no records."
    lines = [f"{title}:"]
    for row in rows[:10]:
        data = row if isinstance(row, dict) else getattr(row, "__dict__", {})
        memory_id = data.get("id", "?")
        scope = data.get("scope", "memory")
        content = str(data.get("content", "")).replace("\n", " ").strip()
        if len(content) > 160:
            content = content[:157].rstrip() + "..."
        lines.append(f"#{memory_id} [{scope}] {content}")
    return "\n".join(lines)


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _format_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _split_sticker_directive(response_text: str) -> tuple[str, str | None]:
    lines = response_text.splitlines()
    kept: list[str] = []
    sticker_alias: str | None = None
    for line in lines:
        match = STICKER_DIRECTIVE_RE.match(line)
        if match:
            sticker_alias = match.group(1).strip().lower()
            continue
        stage_match = STICKER_STAGE_DIRECTION_RE.match(line)
        if stage_match:
            sticker_alias = (stage_match.group("alias") or "any").strip().lower()
            continue
        kept.append(line)
    response = "\n".join(kept).strip()
    trailing_match = TRAILING_STICKER_STAGE_DIRECTION_RE.search(response)
    if trailing_match:
        sticker_alias = (trailing_match.group("alias") or "any").strip().lower()
        response = response[: trailing_match.start()].strip()
    return response, sticker_alias


def _sticker_alias_candidates(alias: str, *, allow_any: bool = True) -> list[str]:
    normalized = alias.strip().lower().replace("-", "_")
    fallbacks = STICKER_ALIAS_FALLBACKS.get(normalized, ())
    candidates = _dedupe_aliases([normalized, *fallbacks])
    if allow_any:
        return candidates
    return [
        candidate
        for candidate in candidates
        if candidate not in {"any", "default", "saved"}
    ]


def _resolve_sticker_alias_for_context(
    alias: str,
    *,
    user_text: str,
    response_text: str,
) -> str:
    normalized = alias.strip().lower().replace("-", "_")
    if _is_angry_roleplay_context(user_text, response_text) and normalized in {
        "any",
        "default",
        "saved",
        "confused",
        "thinking",
        "chill",
        "warning",
    }:
        return "angry"
    return normalized


def _is_angry_roleplay_context(user_text: str, response_text: str) -> bool:
    user = user_text.lower()
    response = response_text.lower()
    angry_terms = ("angry", "mad", "furious", "annoyed", "rage", "scold")
    roleplay_terms = ("pretend", "act", "roleplay", "sound", "be ", "as if", "fake")
    if any(term in user for term in angry_terms) and (
        any(term in user for term in roleplay_terms) or "angry" in user
    ):
        return True
    if "oi" in response and any(term in response for term in ("move", "focus", "what is this")):
        return True
    return False


def _dedupe_aliases(aliases: list[str] | tuple[str, ...]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for alias in aliases:
        normalized = alias.strip().lower().replace("-", "_")
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped

