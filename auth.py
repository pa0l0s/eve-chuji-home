import os
import base64
from urllib.parse import urlencode

import httpx
from itsdangerous import URLSafeSerializer, BadSignature

EVE_SSO_AUTH_URL = "https://login.eveonline.com/v2/oauth/authorize"
EVE_SSO_TOKEN_URL = "https://login.eveonline.com/v2/oauth/token"
ESI_VERIFY_URL = "https://esi.evetech.net/verify/"

SCOPES = " ".join([
    "publicData",
    "esi-corporations.read_projects.v1",
    "esi-wallet.read_character_wallet.v1",
    "esi-skills.read_skills.v1",
    "esi-characters.read_corporation_roles.v1",
])


def _signer() -> URLSafeSerializer:
    secret = os.getenv("SECRET_KEY", "dev-insecure-key")
    return URLSafeSerializer(secret)


def build_login_url(state: str) -> str:
    params = {
        "response_type": "code",
        "redirect_uri": os.getenv("EVE_CALLBACK_URL"),
        "client_id": os.getenv("EVE_CLIENT_ID"),
        "scope": SCOPES,
        "state": state,
    }
    return f"{EVE_SSO_AUTH_URL}?{urlencode(params)}"


def _credentials() -> str:
    client_id = os.getenv("EVE_CLIENT_ID")
    client_secret = os.getenv("EVE_CLIENT_SECRET")
    return base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()


async def exchange_code(code: str) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.post(
            EVE_SSO_TOKEN_URL,
            headers={
                "Authorization": f"Basic {_credentials()}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"grant_type": "authorization_code", "code": code},
        )
        r.raise_for_status()
        return r.json()


async def verify_token(access_token: str) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.get(
            ESI_VERIFY_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        r.raise_for_status()
        return r.json()


def make_session_cookie(character_id: int) -> str:
    return _signer().dumps({"cid": character_id})


def read_session_cookie(cookie: str) -> int | None:
    if not cookie:
        return None
    try:
        data = _signer().loads(cookie)
        return data["cid"]
    except (BadSignature, KeyError, Exception):
        return None
