import os
import time
import aiosqlite
from pathlib import Path


def _db_path() -> str:
    url = os.getenv("DATABASE_URL", "sqlite:///./data/chuji.db")
    return url.replace("sqlite:///", "")


async def init_db():
    path = _db_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(path) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tokens (
                character_id   INTEGER PRIMARY KEY,
                character_name TEXT    NOT NULL,
                access_token   TEXT    NOT NULL,
                refresh_token  TEXT    NOT NULL,
                expires_at     REAL    NOT NULL,
                corporation_id INTEGER NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS janice_cache (
                item_id   INTEGER PRIMARY KEY,
                buy_price REAL    NOT NULL,
                cached_at REAL    NOT NULL
            )
        """)
        await db.commit()


async def upsert_token(character_id, character_name, access_token, refresh_token, expires_at, corporation_id):
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute("""
            INSERT INTO tokens (character_id, character_name, access_token, refresh_token, expires_at, corporation_id)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(character_id) DO UPDATE SET
                character_name = excluded.character_name,
                access_token   = excluded.access_token,
                refresh_token  = excluded.refresh_token,
                expires_at     = excluded.expires_at,
                corporation_id = excluded.corporation_id
        """, (character_id, character_name, access_token, refresh_token, expires_at, corporation_id))
        await db.commit()


async def get_token(character_id: int) -> dict | None:
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM tokens WHERE character_id = ?", (character_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def delete_token(character_id: int):
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute("DELETE FROM tokens WHERE character_id = ?", (character_id,))
        await db.commit()


async def upsert_janice_cache(item_id: int, buy_price: float, cached_at: float):
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute("""
            INSERT INTO janice_cache (item_id, buy_price, cached_at)
            VALUES (?, ?, ?)
            ON CONFLICT(item_id) DO UPDATE SET
                buy_price = excluded.buy_price,
                cached_at = excluded.cached_at
        """, (item_id, buy_price, cached_at))
        await db.commit()


async def get_janice_cache(item_id: int) -> dict | None:
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM janice_cache WHERE item_id = ?", (item_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None
