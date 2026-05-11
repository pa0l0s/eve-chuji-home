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
        for col in ("created_at REAL", "last_login_at REAL",
                    "last_seen_at REAL", "banned INTEGER DEFAULT 0"):
            try:
                await db.execute(f"ALTER TABLE tokens ADD COLUMN {col}")
            except aiosqlite.OperationalError:
                pass
        await db.execute("""
            CREATE TABLE IF NOT EXISTS janice_cache (
                item_id   INTEGER PRIMARY KEY,
                buy_price REAL    NOT NULL,
                cached_at REAL    NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS structure_cache (
                structure_id INTEGER PRIMARY KEY,
                name         TEXT    NOT NULL,
                cached_at    REAL    NOT NULL
            )
        """)
        for col in ("type_id INTEGER", "system_id INTEGER", "owner_id INTEGER"):
            try:
                await db.execute(f"ALTER TABLE structure_cache ADD COLUMN {col}")
            except aiosqlite.OperationalError:
                pass
        await db.execute("""
            CREATE TABLE IF NOT EXISTS type_cache (
                type_id   INTEGER PRIMARY KEY,
                name      TEXT    NOT NULL,
                group_id  INTEGER,
                cached_at REAL    NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS system_cache (
                system_id       INTEGER PRIMARY KEY,
                name            TEXT    NOT NULL,
                security_status REAL,
                cached_at       REAL    NOT NULL
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


async def update_last_login(character_id: int):
    now = time.time()
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            "UPDATE tokens SET last_login_at = ?, "
            "created_at = COALESCE(created_at, ?) WHERE character_id = ?",
            (now, now, character_id),
        )
        await db.commit()


async def update_last_seen(character_id: int):
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            "UPDATE tokens SET last_seen_at = ? WHERE character_id = ?",
            (time.time(), character_id),
        )
        await db.commit()


async def set_banned(character_id: int, banned: bool):
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            "UPDATE tokens SET banned = ? WHERE character_id = ?",
            (1 if banned else 0, character_id),
        )
        await db.commit()


async def is_banned(character_id: int) -> bool:
    async with aiosqlite.connect(_db_path()) as db:
        async with db.execute(
            "SELECT banned FROM tokens WHERE character_id = ?", (character_id,)
        ) as cur:
            row = await cur.fetchone()
            return bool(row and row[0])


async def list_all_users() -> list[dict]:
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT character_id, character_name, corporation_id,
                   created_at, last_login_at, last_seen_at, banned
            FROM tokens
            ORDER BY last_seen_at DESC, character_name
        """) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_cached_structure(structure_id: int) -> dict | None:
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT name, type_id, system_id, owner_id FROM structure_cache WHERE structure_id = ?",
            (structure_id,),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def cache_structure_name(structure_id: int, name: str,
                               type_id: int | None = None,
                               system_id: int | None = None,
                               owner_id: int | None = None):
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute("""
            INSERT INTO structure_cache (structure_id, name, type_id, system_id, owner_id, cached_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(structure_id) DO UPDATE SET
                name      = excluded.name,
                type_id   = COALESCE(excluded.type_id,   structure_cache.type_id),
                system_id = COALESCE(excluded.system_id, structure_cache.system_id),
                owner_id  = COALESCE(excluded.owner_id,  structure_cache.owner_id),
                cached_at = excluded.cached_at
        """, (structure_id, name, type_id, system_id, owner_id, time.time()))
        await db.commit()


async def list_structures_by_owner(owner_id: int) -> list[dict]:
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT structure_id, name, type_id, system_id "
            "FROM structure_cache WHERE owner_id = ? ORDER BY name",
            (owner_id,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_cached_type(type_id: int) -> dict | None:
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT name, group_id FROM type_cache WHERE type_id = ?", (type_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def cache_type(type_id: int, name: str, group_id: int | None = None):
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute("""
            INSERT INTO type_cache (type_id, name, group_id, cached_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(type_id) DO UPDATE SET
                name = excluded.name,
                group_id = excluded.group_id,
                cached_at = excluded.cached_at
        """, (type_id, name, group_id, time.time()))
        await db.commit()


async def get_cached_system(system_id: int) -> dict | None:
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT name, security_status FROM system_cache WHERE system_id = ?", (system_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def cache_system(system_id: int, name: str, security_status: float | None = None):
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute("""
            INSERT INTO system_cache (system_id, name, security_status, cached_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(system_id) DO UPDATE SET
                name = excluded.name,
                security_status = excluded.security_status,
                cached_at = excluded.cached_at
        """, (system_id, name, security_status, time.time()))
        await db.commit()


async def list_all_character_ids() -> list[int]:
    async with aiosqlite.connect(_db_path()) as db:
        async with db.execute("SELECT character_id FROM tokens") as cur:
            return [r[0] for r in await cur.fetchall()]
