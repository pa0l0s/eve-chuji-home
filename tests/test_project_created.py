import pytest

from db import (
    init_db,
    get_cached_project_created_map,
    cache_project_created_batch,
)


@pytest.fixture(autouse=True)
async def _db():
    await init_db()


async def test_init_db_is_idempotent_for_created_cache():
    await init_db()  # second run must not raise


async def test_empty_cache_returns_empty_map():
    assert await get_cached_project_created_map() == {}


async def test_cache_batch_round_trip():
    await cache_project_created_batch([
        ("uuid-a", "2025-06-01T00:00:00Z"),
        ("uuid-b", "2025-07-15T12:30:00Z"),
    ])
    assert await get_cached_project_created_map() == {
        "uuid-a": "2025-06-01T00:00:00Z",
        "uuid-b": "2025-07-15T12:30:00Z",
    }


async def test_cache_batch_upsert_overwrites():
    await cache_project_created_batch([("uuid-a", "2025-06-01T00:00:00Z")])
    await cache_project_created_batch([("uuid-a", "2099-01-01T00:00:00Z")])
    assert (await get_cached_project_created_map())["uuid-a"] == "2099-01-01T00:00:00Z"


async def test_cache_batch_empty_is_noop():
    await cache_project_created_batch([])
    assert await get_cached_project_created_map() == {}
