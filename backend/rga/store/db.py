"""Async SQLite engine with the PRAGMAs that make concurrent access safe:

  * journal_mode=WAL  -> readers don't block the writer (and vice-versa)
  * busy_timeout=5000 -> a contended write WAITS up to 5s instead of erroring
                         ("database is locked" -> avoided)
  * foreign_keys=ON   -> referential integrity / cascade deletes
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .orm import Base


def _apply_pragmas(dbapi_conn, _record) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL;")
    cur.execute("PRAGMA busy_timeout=5000;")
    cur.execute("PRAGMA synchronous=NORMAL;")
    cur.execute("PRAGMA foreign_keys=ON;")
    cur.close()


class Database:
    def __init__(self, path: str) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_async_engine(
            f"sqlite+aiosqlite:///{p.as_posix()}", future=True
        )
        # PRAGMAs run on every new DBAPI connection.
        event.listen(self.engine.sync_engine, "connect", _apply_pragmas)
        self.session = async_sessionmaker(
            self.engine, expire_on_commit=False, class_=AsyncSession
        )

    async def init(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def reset(self) -> None:
        """Drop every table and recreate the empty schema — a full store wipe."""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)

    async def dispose(self) -> None:
        await self.engine.dispose()
