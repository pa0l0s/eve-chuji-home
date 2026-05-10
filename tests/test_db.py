import time
import pytest
from db import init_db, upsert_token, get_token, delete_token, upsert_janice_cache, get_janice_cache


async def test_init_db_creates_tables():
    await init_db()


async def test_upsert_and_get_token():
    await init_db()
    await upsert_token(
        character_id=123,
        character_name="Test Pilot",
        access_token="acc_token",
        refresh_token="ref_token",
        expires_at=time.time() + 1200,
        corporation_id=98340844,
    )
    row = await get_token(123)
    assert row["character_name"] == "Test Pilot"
    assert row["corporation_id"] == 98340844


async def test_get_token_returns_none_for_missing():
    await init_db()
    assert await get_token(999) is None


async def test_delete_token():
    await init_db()
    await upsert_token(123, "Pilot", "a", "r", time.time() + 1200, 98340844)
    await delete_token(123)
    assert await get_token(123) is None


async def test_upsert_overwrites_token():
    await init_db()
    await upsert_token(123, "Pilot", "old_acc", "old_ref", time.time() + 1200, 98340844)
    await upsert_token(123, "Pilot", "new_acc", "new_ref", time.time() + 1200, 98340844)
    row = await get_token(123)
    assert row["access_token"] == "new_acc"


async def test_upsert_and_get_janice_cache():
    await init_db()
    await upsert_janice_cache(item_id=34, buy_price=4.5, cached_at=time.time())
    row = await get_janice_cache(34)
    assert row["buy_price"] == pytest.approx(4.5)


async def test_get_janice_cache_returns_none_for_missing():
    await init_db()
    assert await get_janice_cache(9999) is None
