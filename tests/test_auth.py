import pytest
import respx
from httpx import Response
from auth import (
    build_login_url,
    exchange_code,
    make_session_cookie,
    read_session_cookie,
    verify_token,
)


def test_build_login_url_contains_client_id():
    url = build_login_url("test_state_123")
    assert "test_client_id" in url
    assert "test_state_123" in url
    assert "login.eveonline.com" in url


def test_session_cookie_roundtrip():
    cookie = make_session_cookie(12345)
    assert read_session_cookie(cookie) == 12345


def test_read_session_cookie_invalid_returns_none():
    assert read_session_cookie("garbage.cookie.value") is None


def test_read_session_cookie_empty_returns_none():
    assert read_session_cookie("") is None


@respx.mock
async def test_exchange_code_returns_tokens():
    respx.post("https://login.eveonline.com/v2/oauth/token").mock(
        return_value=Response(200, json={
            "access_token": "acc_abc",
            "refresh_token": "ref_abc",
            "expires_in": 1200,
        })
    )
    result = await exchange_code("auth_code_xyz")
    assert result["access_token"] == "acc_abc"
    assert result["refresh_token"] == "ref_abc"


def test_verify_token_decodes_jwt_payload():
    import base64, json
    payload = base64.urlsafe_b64encode(
        json.dumps({"sub": "CHARACTER:EVE:9001", "name": "Test Pilot"}).encode()
    ).rstrip(b"=").decode()
    fake_jwt = f"eyJhbGciOiJSUzI1NiJ9.{payload}.fakesig"
    result = verify_token(fake_jwt)
    assert result["CharacterID"] == 9001
    assert result["CharacterName"] == "Test Pilot"
