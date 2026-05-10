import os
import time
import base64
import httpx

from db import get_token, upsert_token, get_cached_structure, cache_structure_name, list_all_character_ids

ESI_BASE = "https://esi.evetech.net/latest"
EVE_SSO_TOKEN_URL = "https://login.eveonline.com/v2/oauth/token"


def _credentials() -> str:
    cid = os.getenv("EVE_CLIENT_ID")
    secret = os.getenv("EVE_CLIENT_SECRET")
    return base64.b64encode(f"{cid}:{secret}".encode()).decode()


async def get_valid_token(character_id: int) -> str:
    row = await get_token(character_id)
    if not row:
        raise ValueError("No token found")
    if time.time() >= row["expires_at"] - 60:
        row = await _refresh(row)
    return row["access_token"]


async def _refresh(row: dict) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.post(
            EVE_SSO_TOKEN_URL,
            headers={
                "Authorization": f"Basic {_credentials()}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"grant_type": "refresh_token", "refresh_token": row["refresh_token"]},
        )
        r.raise_for_status()
        data = r.json()

    updated = {
        **row,
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token", row["refresh_token"]),
        "expires_at": time.time() + data["expires_in"],
    }
    await upsert_token(
        updated["character_id"], updated["character_name"],
        updated["access_token"], updated["refresh_token"],
        updated["expires_at"], updated["corporation_id"],
    )
    return updated


async def get_character(character_id: int, access_token: str) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{ESI_BASE}/characters/{character_id}/",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        r.raise_for_status()
        return r.json()


async def get_wallet(character_id: int, access_token: str) -> float:
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{ESI_BASE}/characters/{character_id}/wallet/",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        r.raise_for_status()
        return r.json()


async def get_skills(character_id: int, access_token: str) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{ESI_BASE}/characters/{character_id}/skills/",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        r.raise_for_status()
        return r.json()


async def get_corp_contracts(corporation_id: int, access_token: str) -> list:
    result = []
    page = 1
    async with httpx.AsyncClient() as client:
        while True:
            r = await client.get(
                f"{ESI_BASE}/corporations/{corporation_id}/contracts/",
                params={"datasource": "tranquility", "page": page},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            r.raise_for_status()
            result.extend(r.json())
            if page >= int(r.headers.get("X-Pages", 1)):
                break
            page += 1
    return result


async def get_corp_projects(corporation_id: int, access_token: str) -> list:
    # Projects API requires compatibility_date >= 2026-01-01.
    result = []
    cursor = None
    async with httpx.AsyncClient() as client:
        while True:
            params = {"compatibility_date": "2026-01-01", "limit": 100}
            if cursor:
                params["after"] = cursor
            r = await client.get(
                f"{ESI_BASE}/corporations/{corporation_id}/projects",
                params=params,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            r.raise_for_status()
            data = r.json()
            result.extend(data.get("projects", []))
            cursor = data.get("cursor", {}).get("after")
            if not cursor:
                break
    return result


async def resolve_names(ids: list[int]) -> dict[int, str]:
    """Resolve character / corp / alliance IDs to names via ESI /universe/names/."""
    clean = [i for i in {*ids} if i]
    if not clean:
        return {}
    async with httpx.AsyncClient() as client:
        try:
            r = await client.post(f"{ESI_BASE}/universe/names/", json=clean)
            r.raise_for_status()
            return {item["id"]: item["name"] for item in r.json()}
        except httpx.HTTPError:
            return {}


async def _fetch_structure(structure_id: int, access_token: str) -> str | None:
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(
                f"{ESI_BASE}/universe/structures/{structure_id}/",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            r.raise_for_status()
            return r.json().get("name")
        except httpx.HTTPError:
            return None


async def get_location_name(location_id: int, access_token: str) -> str:
    # NPC stations: public, no auth.
    if location_id < 1_000_000_000_000:
        async with httpx.AsyncClient() as client:
            try:
                r = await client.get(f"{ESI_BASE}/universe/stations/{location_id}/")
                r.raise_for_status()
                return r.json().get("name", "Unknown station")
            except httpx.HTTPError:
                return f"Station #{str(location_id)[-5:]}"

    # Player citadel: cache → caller's token → other corp members' tokens.
    cached = await get_cached_structure(location_id)
    if cached:
        return cached

    name = await _fetch_structure(location_id, access_token)
    if not name:
        for char_id in await list_all_character_ids():
            try:
                other = await get_valid_token(char_id)
            except Exception:
                continue
            if other == access_token:
                continue
            name = await _fetch_structure(location_id, other)
            if name:
                break

    if name:
        await cache_structure_name(location_id, name)
        return name
    return f"Citadel #{str(location_id)[-5:]}"
