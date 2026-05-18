from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

from dennis_bot.config import Settings
from dennis_bot.db.migrations import run_migrations


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[aiosqlite.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = await aiosqlite.connect(self.path)
        connection.row_factory = aiosqlite.Row
        await connection.execute("PRAGMA foreign_keys = ON")
        await connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
            await connection.commit()
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()


def connect_database(settings: Settings) -> Database:
    return Database(settings.database_path)


async def initialize_database(settings: Settings) -> None:
    database = connect_database(settings)
    async with database.connect() as connection:
        await run_migrations(connection)
