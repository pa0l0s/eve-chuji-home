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


@respx.mock
async def test_verify_token_returns_character_info():
    respx.get("https://esi.evetech.net/verify/").mock(
        return_value=Response(200, json={
            "CharacterID": 9001,
            "CharacterName": "Test Pilot",
        })
    )
    result = await verify_token("acc_abc")
    assert result["CharacterID"] == 9001
