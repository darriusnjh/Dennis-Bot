from __future__ import annotations

from pathlib import Path

import aiosqlite


def _migration_directory() -> Path:
    source_root = Path(__file__).resolve().parents[3] / "migrations"
    candidates = (Path.cwd() / "migrations", source_root)
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    formatted = ", ".join(str(candidate) for candidate in candidates)
    raise FileNotFoundError(f"Could not find database migrations directory. Checked: {formatted}")


async def run_migrations(connection: aiosqlite.Connection) -> None:
    await connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        )
        """
    )
    migration_directory = _migration_directory()
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
