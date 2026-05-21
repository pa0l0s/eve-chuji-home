import respx
from httpx import Response

from esi import get_market_history

HISTORY_URL = "https://esi.evetech.net/latest/markets/10000002/history/"


@respx.mock
async def test_market_history_returns_records_sorted_by_date():
    respx.get(HISTORY_URL).mock(return_value=Response(200, json=[
        {"date": "2026-03-02", "average": 5.2, "highest": 5.5, "lowest": 5.0},
        {"date": "2026-03-01", "average": 5.0, "highest": 5.3, "lowest": 4.8},
    ]))
    out = await get_market_history(34)
    assert [d["date"] for d in out] == ["2026-03-01", "2026-03-02"]


@respx.mock
async def test_market_history_returns_empty_on_http_error():
    respx.get(HISTORY_URL).mock(return_value=Response(404))
    assert await get_market_history(999999) == []


import time as _time

import pytest
from db import init_db, upsert_token
from auth import make_session_cookie


@pytest.fixture(autouse=True)
async def _setup_db():
    await init_db()


async def _auth_cookie(character_id: int = 123) -> dict:
    await upsert_token(
        character_id=character_id,
        character_name="Test Pilot",
        access_token="valid_acc",
        refresh_token="valid_ref",
        expires_at=_time.time() + 1200,
        corporation_id=98340844,
    )
    return {"session": make_session_cookie(character_id)}


async def test_market_history_endpoint_requires_session(client):
    r = await client.get("/api/market/34/history")
    assert r.status_code == 401


@respx.mock
async def test_market_history_endpoint_returns_last_90(client):
    from datetime import date, timedelta
    base = date(2026, 1, 1)
    rows = [{"date": str(base + timedelta(days=i)), "average": float(i),
             "highest": float(i) + 1.0, "lowest": max(0.0, float(i) - 1.0)}
            for i in range(100)]
    respx.get(HISTORY_URL).mock(return_value=Response(200, json=rows))
    cookies = await _auth_cookie()
    r = await client.get("/api/market/34/history", cookies=cookies)
    assert r.status_code == 200
    data = r.json()
    assert data["type_id"] == 34
    assert len(data["history"]) == 90
    assert data["history"][-1]["date"] == str(base + timedelta(days=99))


@respx.mock
async def test_market_history_endpoint_caches(client):
    route = respx.get(HISTORY_URL).mock(return_value=Response(200, json=[
        {"date": "2026-03-01", "average": 5.0, "highest": 5.3, "lowest": 4.8},
        {"date": "2026-03-02", "average": 5.2, "highest": 5.5, "lowest": 5.0},
    ]))
    cookies = await _auth_cookie()
    await client.get("/api/market/77/history", cookies=cookies)
    await client.get("/api/market/77/history", cookies=cookies)
    assert route.call_count == 1
