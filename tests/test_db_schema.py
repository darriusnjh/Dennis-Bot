from pathlib import Path

from dennis_bot.db import Database, run_migrations


async def test_schema_creation_creates_prd_tables(tmp_path: Path) -> None:
    database = Database(tmp_path / "dennis.sqlite3")

    async with database.connect() as connection:
        await run_migrations(connection)
        rows = await connection.execute_fetchall(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table'
            ORDER BY name
            """
        )

    table_names = {row["name"] for row in rows}
    assert {
        "users",
        "chats",
        "conversation_messages",
        "memory_records",
        "memory_sessions",
        "knowledge_states",
        "knowledge_update_jobs",
        "sticker_packs",
        "sticker_aliases",
        "web_monitors",
        "monitor_runs",
        "social_activity_items",
        "processed_telegram_updates",
        "schema_migrations",
    }.issubset(table_names)


async def test_migrations_are_idempotent(tmp_path: Path) -> None:
    database = Database(tmp_path / "dennis.sqlite3")

    async with database.connect() as connection:
        await run_migrations(connection)
        await run_migrations(connection)
        rows = await connection.execute_fetchall("SELECT version FROM schema_migrations")

    assert [row["version"] for row in rows] == [
        "0001_initial.sql",
        "0002_processed_telegram_updates.sql",
    ]
