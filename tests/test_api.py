import time
import pytest
from db import init_db, upsert_token
from auth import make_session_cookie


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


async def test_root_returns_html(client):
    r = await client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


async def test_auth_me_returns_401_without_session(client):
    r = await client.get("/api/auth/me")
    assert r.status_code == 401


async def test_auth_me_returns_character_info(client):
    cookies = await _auth_cookie()
    r = await client.get("/api/auth/me", cookies=cookies)
    assert r.status_code == 200
    data = r.json()
    assert data["character_id"] == 123
    assert data["character_name"] == "Test Pilot"


async def test_auth_login_redirects_to_eve_sso(client):
    r = await client.get("/api/auth/login", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert "login.eveonline.com" in r.headers["location"]


async def test_auth_logout_clears_session(client):
    cookies = await _auth_cookie()
    r = await client.get("/api/auth/logout", cookies=cookies, follow_redirects=False)
    assert r.status_code in (302, 307)
    # session cookie should be cleared (empty value or deleted)
    assert "session" not in r.cookies or r.cookies["session"] == ""


async def test_contracts_returns_401_without_auth(client):
    r = await client.get("/api/contracts")
    assert r.status_code == 401


async def test_projects_returns_401_without_auth(client):
    r = await client.get("/api/projects")
    assert r.status_code == 401


async def test_structures_returns_401_without_auth(client):
    r = await client.get("/api/structures")
    assert r.status_code == 401


async def test_starbase_detail_returns_401_without_auth(client):
    r = await client.get("/api/starbases/1234567?system_id=30000142")
    assert r.status_code == 401


async def test_member_returns_401_without_auth(client):
    r = await client.get("/api/member")
    assert r.status_code == 401
