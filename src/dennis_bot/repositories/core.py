from __future__ import annotations

import hashlib
from typing import Any

import aiosqlite


Row = dict[str, Any]


def _row(row: aiosqlite.Row | None) -> Row | None:
    return dict(row) if row is not None else None


async def _fetchone(connection: aiosqlite.Connection, sql: str, values: tuple[Any, ...]) -> Row | None:
    cursor = await connection.execute(sql, values)
    try:
        return _row(await cursor.fetchone())
    finally:
        await cursor.close()


async def _fetchall(connection: aiosqlite.Connection, sql: str, values: tuple[Any, ...] = ()) -> list[Row]:
    cursor = await connection.execute(sql, values)
    try:
        return [dict(row) for row in await cursor.fetchall()]
    finally:
        await cursor.close()


class UserRepository:
    def __init__(self, connection: aiosqlite.Connection) -> None:
        self.connection = connection

    async def upsert(
        self,
        telegram_user_id: int,
        *,
        display_name: str | None = None,
        role: str = "member",
        trusted_group_memory_access: bool = False,
    ) -> Row:
        await self.connection.execute(
            """
            INSERT INTO users (
                telegram_user_id, display_name, role, trusted_group_memory_access
            )
            VALUES (?, ?, ?, ?)
            ON CONFLICT(telegram_user_id) DO UPDATE SET
                display_name = excluded.display_name,
                role = excluded.role,
                trusted_group_memory_access = excluded.trusted_group_memory_access,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            """,
            (telegram_user_id, display_name, role, int(trusted_group_memory_access)),
        )
        row = await self.get(telegram_user_id)
        assert row is not None
        return row

    async def get(self, telegram_user_id: int) -> Row | None:
        return await _fetchone(
            self.connection,
            "SELECT * FROM users WHERE telegram_user_id = ?",
            (telegram_user_id,),
        )


class ChatRepository:
    def __init__(self, connection: aiosqlite.Connection) -> None:
        self.connection = connection

    async def upsert(
        self,
        telegram_chat_id: int,
        *,
        chat_type: str,
        title: str | None = None,
        memory_policy: str = "auto",
        full_memory_access_enabled: bool = False,
        sticker_policy: str = "contextual",
        notifications_enabled: bool = True,
        default_monitor_notifications: bool = True,
    ) -> Row:
        await self.connection.execute(
            """
            INSERT INTO chats (
                telegram_chat_id, chat_type, title, memory_policy, full_memory_access_enabled,
                sticker_policy, notifications_enabled, default_monitor_notifications
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(telegram_chat_id) DO UPDATE SET
                chat_type = excluded.chat_type,
                title = excluded.title,
                memory_policy = excluded.memory_policy,
                full_memory_access_enabled = excluded.full_memory_access_enabled,
                sticker_policy = excluded.sticker_policy,
                notifications_enabled = excluded.notifications_enabled,
                default_monitor_notifications = excluded.default_monitor_notifications,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            """,
            (
                telegram_chat_id,
                chat_type,
                title,
                memory_policy,
                int(full_memory_access_enabled),
                sticker_policy,
                int(notifications_enabled),
                int(default_monitor_notifications),
            ),
        )
        row = await self.get(telegram_chat_id)
        assert row is not None
        return row

    async def get(self, telegram_chat_id: int) -> Row | None:
        return await _fetchone(
            self.connection,
            "SELECT * FROM chats WHERE telegram_chat_id = ?",
            (telegram_chat_id,),
        )

    async def set_active_knowledge_state(self, telegram_chat_id: int, knowledge_state_id: int | None) -> None:
        await self.connection.execute(
            """
            INSERT OR IGNORE INTO chats (telegram_chat_id, chat_type)
            VALUES (?, 'direct')
            """,
            (telegram_chat_id,),
        )
        await self.connection.execute(
            """
            UPDATE chats
            SET active_knowledge_state_id = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE telegram_chat_id = ?
            """,
            (knowledge_state_id, telegram_chat_id),
        )
        await self.connection.commit()


class ConversationMessageRepository:
    def __init__(self, connection: aiosqlite.Connection) -> None:
        self.connection = connection

    async def add(
        self,
        *,
        telegram_chat_id: int,
        direction: str,
        content: str | None,
        telegram_user_id: int | None = None,
        telegram_message_id: int | None = None,
        message_type: str = "text",
        simplemem_session_id: str | None = None,
        included_in_simplemem: bool = False,
        memory_extraction_status: str = "pending",
    ) -> Row:
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest() if content is not None else None
        cursor = await self.connection.execute(
            """
            INSERT INTO conversation_messages (
                telegram_chat_id, telegram_user_id, telegram_message_id, direction, message_type,
                content, content_hash, memory_extraction_status, simplemem_session_id,
                included_in_simplemem
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                telegram_chat_id,
                telegram_user_id,
                telegram_message_id,
                direction,
                message_type,
                content,
                content_hash,
                memory_extraction_status,
                simplemem_session_id,
                int(included_in_simplemem),
            ),
        )
        await self.connection.commit()
        return await self.get(int(cursor.lastrowid))

    async def get(self, message_id: int) -> Row:
        row = await _fetchone(self.connection, "SELECT * FROM conversation_messages WHERE id = ?", (message_id,))
        assert row is not None
        return row

    async def list_for_chat(self, telegram_chat_id: int, *, limit: int = 50) -> list[Row]:
        return await _fetchall(
            self.connection,
            """
            SELECT * FROM conversation_messages
            WHERE telegram_chat_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (telegram_chat_id, limit),
        )


class MemoryRepository:
    def __init__(self, connection: aiosqlite.Connection) -> None:
        self.connection = connection

    async def add(
        self,
        *,
        scope: str,
        content: str,
        owner_user_id: int | None = None,
        chat_id: int | None = None,
        simplemem_tenant_id: str | None = None,
        simplemem_session_id: str | None = None,
        simplemem_entry_id: str | None = None,
        tags: str | None = None,
        importance: float | None = None,
        confidence: float | None = None,
        sensitivity: str | None = None,
        source_message_id: int | None = None,
    ) -> Row:
        cursor = await self.connection.execute(
            """
            INSERT INTO memory_records (
                scope, owner_user_id, chat_id, simplemem_tenant_id, simplemem_session_id,
                simplemem_entry_id, content, tags, importance, confidence, sensitivity, source_message_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                scope,
                owner_user_id,
                chat_id,
                simplemem_tenant_id,
                simplemem_session_id,
                simplemem_entry_id,
                content,
                tags,
                importance,
                confidence,
                sensitivity,
                source_message_id,
            ),
        )
        await self.connection.commit()
        return await self.get(int(cursor.lastrowid))

    async def get(self, memory_id: int, *, include_deleted: bool = False) -> Row:
        deleted_clause = "" if include_deleted else " AND deleted_at IS NULL"
        row = await _fetchone(
            self.connection,
            f"SELECT * FROM memory_records WHERE id = ?{deleted_clause}",
            (memory_id,),
        )
        assert row is not None
        return row

    async def search(self, query: str, *, chat_id: int | None = None, limit: int = 20) -> list[Row]:
        values: list[Any] = [f"%{query}%"]
        chat_clause = ""
        if chat_id is not None:
            chat_clause = " AND chat_id = ?"
            values.append(chat_id)
        values.append(limit)
        return await _fetchall(
            self.connection,
            f"""
            SELECT * FROM memory_records
            WHERE deleted_at IS NULL AND content LIKE ?{chat_clause}
            ORDER BY importance DESC NULLS LAST, updated_at DESC
            LIMIT ?
            """,
            tuple(values),
        )

    async def list(
        self,
        *,
        scope: str | None = None,
        chat_id: int | None = None,
        owner_user_id: int | None = None,
        limit: int = 50,
    ) -> list[Row]:
        clauses = ["deleted_at IS NULL"]
        values: list[Any] = []
        if scope is not None:
            clauses.append("scope = ?")
            values.append(scope)
        if chat_id is not None:
            clauses.append("chat_id = ?")
            values.append(chat_id)
        if owner_user_id is not None:
            clauses.append("owner_user_id = ?")
            values.append(owner_user_id)
        values.append(limit)
        return await _fetchall(
            self.connection,
            f"""
            SELECT * FROM memory_records
            WHERE {" AND ".join(clauses)}
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            tuple(values),
        )

    async def soft_delete(self, memory_id: int) -> None:
        await self.connection.execute(
            """
            UPDATE memory_records
            SET deleted_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = ? AND deleted_at IS NULL
            """,
            (memory_id,),
        )
        await self.connection.commit()


class MemorySessionRepository:
    def __init__(self, connection: aiosqlite.Connection) -> None:
        self.connection = connection

    async def create(
        self,
        *,
        telegram_chat_id: int,
        simplemem_tenant_id: str,
        simplemem_project: str,
        simplemem_memory_session_id: str | None = None,
        content_session_id: str | None = None,
        max_message_count: int = 30,
    ) -> Row:
        cursor = await self.connection.execute(
            """
            INSERT INTO memory_sessions (
                telegram_chat_id, simplemem_tenant_id, simplemem_project,
                simplemem_memory_session_id, content_session_id, max_message_count
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                telegram_chat_id,
                simplemem_tenant_id,
                simplemem_project,
                simplemem_memory_session_id,
                content_session_id,
                max_message_count,
            ),
        )
        return await self.get(int(cursor.lastrowid))

    async def get(self, session_id: int) -> Row:
        row = await _fetchone(self.connection, "SELECT * FROM memory_sessions WHERE id = ?", (session_id,))
        assert row is not None
        return row

    async def get_active_for_chat(self, telegram_chat_id: int) -> Row | None:
        return await _fetchone(
            self.connection,
            """
            SELECT * FROM memory_sessions
            WHERE telegram_chat_id = ? AND status = 'active'
            ORDER BY started_at DESC, id DESC
            LIMIT 1
            """,
            (telegram_chat_id,),
        )

    async def list_active(self) -> list[Row]:
        return await _fetchall(
            self.connection,
            """
            SELECT * FROM memory_sessions
            WHERE status = 'active'
            ORDER BY started_at ASC, id ASC
            """,
        )

    async def increment_message_count(self, session_id: int, amount: int = 1) -> Row:
        await self.connection.execute(
            "UPDATE memory_sessions SET message_count = message_count + ? WHERE id = ?",
            (amount, session_id),
        )
        return await self.get(session_id)

    async def mark_finalizing(self, session_id: int) -> None:
        await self.connection.execute(
            "UPDATE memory_sessions SET status = 'finalizing' WHERE id = ?",
            (session_id,),
        )

    async def finalize(self, session_id: int, *, finalization_report_ref: str | None = None) -> None:
        await self.connection.execute(
            """
            UPDATE memory_sessions
            SET status = 'finalized',
                finalized_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                finalization_report_ref = ?
            WHERE id = ?
            """,
            (finalization_report_ref, session_id),
        )

    async def fail(self, session_id: int, error_message: str) -> None:
        await self.connection.execute(
            "UPDATE memory_sessions SET status = 'failed', error_message = ? WHERE id = ?",
            (error_message, session_id),
        )


class KnowledgeRepository:
    def __init__(self, connection: aiosqlite.Connection) -> None:
        self.connection = connection

    async def upsert_state(
        self,
        *,
        name: str,
        description: str | None = None,
        access_scope: str = "global",
        version: int = 1,
        enabled: bool = True,
        source_refs: str | None = None,
        index_status: str = "indexed",
        last_updated_by_agent_id: str | None = None,
    ) -> Row:
        await self.connection.execute(
            """
            INSERT INTO knowledge_states (
                name, description, access_scope, version, enabled, source_refs,
                index_status, last_indexed_at, last_updated_by_agent_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), ?)
            ON CONFLICT(name, version) DO UPDATE SET
                description = excluded.description,
                access_scope = excluded.access_scope,
                enabled = excluded.enabled,
                source_refs = excluded.source_refs,
                index_status = excluded.index_status,
                last_indexed_at = excluded.last_indexed_at,
                last_updated_by_agent_id = excluded.last_updated_by_agent_id,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            """,
            (
                name,
                description,
                access_scope,
                version,
                int(enabled),
                source_refs,
                index_status,
                last_updated_by_agent_id,
            ),
        )
        await self.connection.commit()
        row = await self.get_state_by_name(name, version=version)
        assert row is not None
        return row

    async def create_state(
        self,
        *,
        name: str,
        description: str | None = None,
        access_scope: str = "global",
        version: int = 1,
        enabled: bool = True,
        source_refs: str | None = None,
        index_status: str = "pending",
    ) -> Row:
        cursor = await self.connection.execute(
            """
            INSERT INTO knowledge_states (
                name, description, access_scope, version, enabled, source_refs, index_status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (name, description, access_scope, version, int(enabled), source_refs, index_status),
        )
        await self.connection.commit()
        return await self.get_state(int(cursor.lastrowid))

    async def get_state(self, state_id: int) -> Row:
        row = await _fetchone(self.connection, "SELECT * FROM knowledge_states WHERE id = ?", (state_id,))
        assert row is not None
        return row

    async def get_state_by_name(self, name: str, *, version: int | None = None) -> Row | None:
        if version is None:
            return await _fetchone(
                self.connection,
                """
                SELECT * FROM knowledge_states
                WHERE name = ?
                ORDER BY version DESC, id DESC
                LIMIT 1
                """,
                (name,),
            )
        return await _fetchone(
            self.connection,
            "SELECT * FROM knowledge_states WHERE name = ? AND version = ?",
            (name, version),
        )

    async def list_states(self, *, enabled_only: bool = False) -> list[Row]:
        clause = "WHERE enabled = 1" if enabled_only else ""
        return await _fetchall(
            self.connection,
            f"SELECT * FROM knowledge_states {clause} ORDER BY name, version DESC",
        )

    async def create_update_job(
        self,
        *,
        source_type: str,
        source_monitor_id: int | None = None,
        source_url: str | None = None,
        detected_change_ref: str | None = None,
        impact_classification: str | None = None,
        status: str = "queued",
        summary: str | None = None,
        knowledge_state_id: int | None = None,
        previous_version: int | None = None,
        new_version: int | None = None,
    ) -> Row:
        cursor = await self.connection.execute(
            """
            INSERT INTO knowledge_update_jobs (
                source_monitor_id, source_url, source_type, detected_change_ref,
                impact_classification, status, summary, knowledge_state_id,
                previous_version, new_version
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_monitor_id,
                source_url,
                source_type,
                detected_change_ref,
                impact_classification,
                status,
                summary,
                knowledge_state_id,
                previous_version,
                new_version,
            ),
        )
        await self.connection.commit()
        return await self.get_update_job(int(cursor.lastrowid))

    async def get_update_job(self, job_id: int) -> Row:
        row = await _fetchone(self.connection, "SELECT * FROM knowledge_update_jobs WHERE id = ?", (job_id,))
        assert row is not None
        return row

    async def update_job_status(
        self,
        job_id: int,
        status: str,
        *,
        summary: str | None = None,
        impact_classification: str | None = None,
        knowledge_state_id: int | None = None,
        previous_version: int | None = None,
        new_version: int | None = None,
    ) -> Row:
        completed = ", completed_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')" if status in {
            "applied",
            "pending_review",
            "rejected",
            "failed",
        } else ""
        await self.connection.execute(
            f"""
            UPDATE knowledge_update_jobs
            SET status = ?,
                summary = COALESCE(?, summary),
                impact_classification = COALESCE(?, impact_classification),
                knowledge_state_id = COALESCE(?, knowledge_state_id),
                previous_version = COALESCE(?, previous_version),
                new_version = COALESCE(?, new_version)
                {completed}
            WHERE id = ?
            """,
            (
                status,
                summary,
                impact_classification,
                knowledge_state_id,
                previous_version,
                new_version,
                job_id,
            ),
        )
        await self.connection.commit()
        return await self.get_update_job(job_id)


class StickerRepository:
    def __init__(self, connection: aiosqlite.Connection) -> None:
        self.connection = connection

    async def upsert_pack(self, *, pack_name: str, title: str | None = None, enabled: bool = True) -> Row:
        await self.connection.execute(
            """
            INSERT INTO sticker_packs (pack_name, title, enabled)
            VALUES (?, ?, ?)
            ON CONFLICT(pack_name) DO UPDATE SET
                title = excluded.title,
                enabled = excluded.enabled,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            """,
            (pack_name, title, int(enabled)),
        )
        row = await _fetchone(self.connection, "SELECT * FROM sticker_packs WHERE pack_name = ?", (pack_name,))
        assert row is not None
        return row

    async def upsert_alias(
        self,
        *,
        pack_id: int,
        alias: str,
        file_id: str,
        emoji: str | None = None,
        tags: str | None = None,
        enabled: bool = True,
    ) -> Row:
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
            (pack_id, alias, file_id, emoji, tags, int(enabled)),
        )
        row = await _fetchone(
            self.connection,
            "SELECT * FROM sticker_aliases WHERE pack_id = ? AND alias = ?",
            (pack_id, alias),
        )
        assert row is not None
        return row

    async def get_alias(self, alias: str) -> Row | None:
        return await _fetchone(
            self.connection,
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
            (alias,),
        )


class MonitorRepository:
    def __init__(self, connection: aiosqlite.Connection) -> None:
        self.connection = connection

    async def upsert_monitor(
        self,
        *,
        name: str,
        url: str,
        schedule: str,
        change_detection_strategy: str,
        monitor_type: str = "website",
        provider: str = "direct",
        target_chat_id: int | None = None,
        relevance_filter: str | None = None,
        impact_policy: str | None = None,
        notify_on_any_change: bool = True,
        knowledge_update_enabled: bool = False,
        source_handle: str | None = None,
        enabled: bool = True,
    ) -> Row:
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
                enabled = excluded.enabled,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            """,
            (
                name,
                url,
                monitor_type,
                provider,
                schedule,
                change_detection_strategy,
                relevance_filter,
                impact_policy,
                int(notify_on_any_change),
                int(knowledge_update_enabled),
                target_chat_id,
                source_handle,
                int(enabled),
            ),
        )
        row = await _fetchone(self.connection, "SELECT * FROM web_monitors WHERE name = ?", (name,))
        assert row is not None
        return row

    async def get_monitor(self, monitor_id: int) -> Row:
        row = await _fetchone(self.connection, "SELECT * FROM web_monitors WHERE id = ?", (monitor_id,))
        assert row is not None
        return row

    async def update_last_seen(
        self,
        monitor_id: int,
        *,
        external_id: str | None = None,
        permalink: str | None = None,
        content_hash: str | None = None,
        content_ref: str | None = None,
        published_at: str | None = None,
        notified: bool = False,
    ) -> None:
        notified_sql = ", last_notified_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')" if notified else ""
        await self.connection.execute(
            f"""
            UPDATE web_monitors
            SET last_seen_external_id = COALESCE(?, last_seen_external_id),
                last_seen_permalink = COALESCE(?, last_seen_permalink),
                last_seen_hash = COALESCE(?, last_seen_hash),
                last_seen_content_ref = COALESCE(?, last_seen_content_ref),
                last_seen_published_at = COALESCE(?, last_seen_published_at),
                last_checked_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                {notified_sql}
            WHERE id = ?
            """,
            (external_id, permalink, content_hash, content_ref, published_at, monitor_id),
        )

    async def create_run(
        self,
        *,
        web_monitor_id: int,
        provider: str,
        request_mode: str,
        provider_endpoint: str | None = None,
        provider_request_id: str | None = None,
        provider_snapshot_id: str | None = None,
        status: str = "queued",
    ) -> Row:
        cursor = await self.connection.execute(
            """
            INSERT INTO monitor_runs (
                web_monitor_id, provider, provider_endpoint, provider_request_id,
                provider_snapshot_id, request_mode, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                web_monitor_id,
                provider,
                provider_endpoint,
                provider_request_id,
                provider_snapshot_id,
                request_mode,
                status,
            ),
        )
        row = await _fetchone(self.connection, "SELECT * FROM monitor_runs WHERE id = ?", (cursor.lastrowid,))
        assert row is not None
        return row

    async def finish_run(
        self,
        run_id: int,
        *,
        status: str,
        records_returned: int = 0,
        records_changed: int = 0,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        await self.connection.execute(
            """
            UPDATE monitor_runs
            SET status = ?,
                records_returned = ?,
                records_changed = ?,
                completed_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                error_code = ?,
                error_message = ?
            WHERE id = ?
            """,
            (status, records_returned, records_changed, error_code, error_message, run_id),
        )

    async def add_social_activity(
        self,
        *,
        web_monitor_id: int,
        external_id: str,
        activity_type: str,
        platform: str = "instagram",
        actor_handle: str | None = None,
        permalink: str | None = None,
        media_type: str = "unknown",
        caption: str | None = None,
        caption_hash: str | None = None,
        published_at: str | None = None,
        thumbnail_ref: str | None = None,
        engagement_snapshot: str | None = None,
        raw_provider_record_ref: str | None = None,
    ) -> Row | None:
        cursor = await self.connection.execute(
            """
            INSERT OR IGNORE INTO social_activity_items (
                web_monitor_id, platform, external_id, activity_type, actor_handle, permalink,
                media_type, caption, caption_hash, published_at, thumbnail_ref,
                engagement_snapshot, raw_provider_record_ref
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                web_monitor_id,
                platform,
                external_id,
                activity_type,
                actor_handle,
                permalink,
                media_type,
                caption,
                caption_hash or "",
                published_at,
                thumbnail_ref,
                engagement_snapshot,
                raw_provider_record_ref,
            ),
        )
        if cursor.rowcount == 0:
            return None
        row = await _fetchone(
            self.connection,
            "SELECT * FROM social_activity_items WHERE id = ?",
            (cursor.lastrowid,),
        )
        assert row is not None
        return row
