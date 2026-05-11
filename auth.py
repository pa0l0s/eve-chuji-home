import os
import json
import base64
from urllib.parse import urlencode

import httpx
from itsdangerous import URLSafeSerializer, BadSignature

EVE_SSO_AUTH_URL = "https://login.eveonline.com/v2/oauth/authorize"
EVE_SSO_TOKEN_URL = "https://login.eveonline.com/v2/oauth/token"

SCOPES = " ".join([
    "publicData",
    "esi-contracts.read_character_contracts.v1",
    "esi-contracts.read_corporation_contracts.v1",
    "esi-corporations.read_projects.v1",
    "esi-corporations.read_starbases.v1",
    "esi-corporations.read_structures.v1",
    "esi-universe.read_structures.v1",
    "esi-location.read_location.v1",
    "esi-location.read_online.v1",
    "esi-location.read_ship_type.v1",
    "esi-ui.open_window.v1",
    # "esi-wallet.read_character_wallet.v1",  # temporarily disabled
    "esi-skills.read_skills.v1",
    "esi-skills.read_skillqueue.v1",
    "esi-characters.read_corporation_roles.v1",
])


def get_admin_character_ids() -> set[int]:
    raw = os.getenv("ADMIN_CHARACTER_IDS", "")
    return {int(s.strip()) for s in raw.split(",") if s.strip().isdigit()}


def is_admin(character_id: int) -> bool:
    return character_id in get_admin_character_ids()


def _signer() -> URLSafeSerializer:
    secret = os.getenv("SECRET_KEY")
    if not secret:
        raise RuntimeError("SECRET_KEY environment variable is not set")
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
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": os.getenv("EVE_CALLBACK_URL"),
            },
        )
        r.raise_for_status()
        return r.json()


def verify_token(access_token: str) -> dict:
    # Decode JWT payload without network call — ESI /verify/ is deprecated.
    # The EVE SSO v2 access token is a signed JWT; we trust it since it came
    # directly from the OAuth2 token exchange.
    parts = access_token.split(".")
    padded = parts[1] + "=" * (4 - len(parts[1]) % 4)
    payload = json.loads(base64.urlsafe_b64decode(padded))
    # sub format: "CHARACTER:EVE:{characterId}"
    character_id = int(payload["sub"].split(":")[-1])
    return {"CharacterID": character_id, "CharacterName": payload["name"]}


def make_session_cookie(character_id: int) -> str:
    return _signer().dumps({"cid": character_id})


def read_session_cookie(cookie: str) -> int | None:
    if not cookie:
        return None
    try:
        data = _signer().loads(cookie)
        return data["cid"]
    except (BadSignature, KeyError):
        return None
