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


import time

from db import upsert_token, get_cached_project_created_map
from auth import make_session_cookie


async def _user_cookie(character_id: int = 777) -> dict:
    await upsert_token(
        character_id=character_id, character_name="Pilot",
        access_token="acc", refresh_token="ref",
        expires_at=time.time() + 1200, corporation_id=98340844,
    )
    return {"session": make_session_cookie(character_id)}


PROJECTS_URL = f"https://esi.evetech.net/corporations/{CORP}/projects"
IDS_URL = "https://esi.evetech.net/latest/universe/ids/"


def _detail_url(pid: str) -> str:
    return f"https://esi.evetech.net/corporations/{CORP}/projects/{pid}"


@respx.mock
async def test_projects_first_call_fetches_and_caches_created(client):
    respx.get(PROJECTS_URL).mock(return_value=Response(200, json={"projects": [
        {"id": "p1", "name": "P1", "state": "Active",
         "last_modified": "2025-08-26T00:00:00Z",
         "progress": {"current": 5, "desired": 10}},
    ]}))
    respx.post(IDS_URL).mock(return_value=Response(200, json={}))
    respx.get(_detail_url("p1")).mock(return_value=Response(200, json={
        "id": "p1", "name": "P1", "state": "Active",
        "last_modified": "2025-08-26T00:00:00Z",
        "details": {"created": "2025-06-01T00:00:00Z"},
        "progress": {"current": 5, "desired": 10},
    }))
    cookies = await _user_cookie()
    r = await client.get("/api/projects", cookies=cookies)
    assert r.status_code == 200
    proj = next(p for p in r.json()["active"] if p["id"] == "p1")
    assert proj["created"] == "2025-06-01T00:00:00Z"
    # And it's persisted in the cache for next time.
    assert (await get_cached_project_created_map())["p1"] == "2025-06-01T00:00:00Z"


@respx.mock
async def test_projects_second_call_serves_created_from_cache(client):
    # Prime the cache; deliberately omit the detail mock — if the endpoint
    # tries to fetch detail, respx.mock will raise.
    await cache_project_created_batch([("p1", "2025-06-01T00:00:00Z")])
    respx.get(PROJECTS_URL).mock(return_value=Response(200, json={"projects": [
        {"id": "p1", "name": "P1", "state": "Active",
         "last_modified": "2025-08-26T00:00:00Z",
         "progress": {"current": 5, "desired": 10}},
    ]}))
    respx.post(IDS_URL).mock(return_value=Response(200, json={}))
    cookies = await _user_cookie()
    r = await client.get("/api/projects", cookies=cookies)
    assert r.status_code == 200
    proj = next(p for p in r.json()["active"] if p["id"] == "p1")
    assert proj["created"] == "2025-06-01T00:00:00Z"


@respx.mock
async def test_projects_detail_failure_omits_created(client):
    respx.get(PROJECTS_URL).mock(return_value=Response(200, json={"projects": [
        {"id": "p1", "name": "P1", "state": "Active",
         "last_modified": "2025-08-26T00:00:00Z",
         "progress": {"current": 5, "desired": 10}},
    ]}))
    respx.post(IDS_URL).mock(return_value=Response(200, json={}))
    respx.get(_detail_url("p1")).mock(return_value=Response(502, json={"error": "boom"}))
    cookies = await _user_cookie()
    r = await client.get("/api/projects", cookies=cookies)
    assert r.status_code == 200
    proj = next(p for p in r.json()["active"] if p["id"] == "p1")
    assert "created" not in proj
    # No row written to the cache when the detail fetch fails.
    assert "p1" not in await get_cached_project_created_map()
