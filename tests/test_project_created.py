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


import respx
from httpx import Response

from esi import get_corp_project_detail

CORP = 98340844  # conftest sets CORP_ID=98340844
PROJECT_URL = f"https://esi.evetech.net/corporations/{CORP}/projects/p1"


@respx.mock
async def test_get_corp_project_detail_returns_json():
    respx.get(PROJECT_URL).mock(return_value=Response(200, json={
        "id": "p1",
        "name": "P1",
        "state": "Active",
        "last_modified": "2025-08-26T00:00:00Z",
        "details": {"created": "2025-06-01T00:00:00Z"},
        "progress": {"current": 5, "desired": 10},
    }))
    detail = await get_corp_project_detail(CORP, "p1", "tok")
    assert detail["details"]["created"] == "2025-06-01T00:00:00Z"


@respx.mock
async def test_get_corp_project_detail_raises_on_http_error():
    respx.get(PROJECT_URL).mock(return_value=Response(500, json={"error": "x"}))
    with pytest.raises(Exception):
        await get_corp_project_detail(CORP, "p1", "tok")
