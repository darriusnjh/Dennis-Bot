from __future__ import annotations

from pathlib import Path

import aiosqlite


async def run_migrations(connection: aiosqlite.Connection) -> None:
    await connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        )
        """
    )
    migration_directory = Path(__file__).resolve().parents[3] / "migrations"
    migration_paths = sorted(path for path in migration_directory.iterdir() if path.name.endswith(".sql"))
    for migration_path in migration_paths:
        version = migration_path.name
        cursor = await connection.execute(
            "SELECT version FROM schema_migrations WHERE version = ?",
            (version,),
        )
        existing = await cursor.fetchone()
        await cursor.close()
        if existing:
            continue
        sql = migration_path.read_text(encoding="utf-8")
        await connection.executescript(sql)
        await connection.execute("INSERT INTO schema_migrations (version) VALUES (?)", (version,))
