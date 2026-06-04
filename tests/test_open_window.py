import time

import pytest
import respx
from httpx import Response

from db import init_db, upsert_token
from auth import make_session_cookie

INFO_URL = "https://esi.evetech.net/ui/openwindow/information"
MARKET_URL = "https://esi.evetech.net/ui/openwindow/marketdetails"


@pytest.fixture(autouse=True)
async def setup_db():
    await init_db()


async def _auth_cookie(character_id=123) -> dict:
    await upsert_token(
        character_id=character_id,
        character_name="Test Pilot",
        access_token="valid_acc",
        refresh_token="valid_ref",
        expires_at=time.time() + 1200,
        corporation_id=98340844,
    )
    return {"session": make_session_cookie(character_id)}


async def test_open_information_requires_auth(client):
    r = await client.post("/api/ui/open-information/34")
    assert r.status_code == 401


async def test_open_market_requires_auth(client):
    r = await client.post("/api/ui/open-market/34")
    assert r.status_code == 401


@respx.mock
async def test_open_information_happy_path(client):
    route = respx.post(INFO_URL).mock(return_value=Response(204))
    cookies = await _auth_cookie()
    r = await client.post("/api/ui/open-information/587", cookies=cookies)
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert route.called
    assert route.calls.last.request.url.params["target_id"] == "587"


@respx.mock
async def test_open_market_happy_path(client):
    route = respx.post(MARKET_URL).mock(return_value=Response(204))
    cookies = await _auth_cookie()
    r = await client.post("/api/ui/open-market/587", cookies=cookies)
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert route.called
    assert route.calls.last.request.url.params["type_id"] == "587"


@respx.mock
async def test_open_information_403_maps_to_scope_message(client):
    respx.post(INFO_URL).mock(return_value=Response(403, json={}))
    cookies = await _auth_cookie()
    r = await client.post("/api/ui/open-information/587", cookies=cookies)
    assert r.status_code == 403
    assert "esi-ui.open_window.v1" in r.json()["detail"]


@respx.mock
async def test_open_market_esi_failure_maps_to_502(client):
    respx.post(MARKET_URL).mock(return_value=Response(500, json={}))
    cookies = await _auth_cookie()
    r = await client.post("/api/ui/open-market/587", cookies=cookies)
    assert r.status_code == 502
