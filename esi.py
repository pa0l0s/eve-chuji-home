import ast
import os
import asyncio
import re
import time
import base64
import httpx

from db import (
    get_token, upsert_token,
    get_cached_structure, cache_structure_name,
    get_cached_type, cache_type,
    get_cached_system, cache_system,
    get_ship_types_cache, set_ship_types_cache,
    list_all_character_ids,
)

ESI_BASE = "https://esi.evetech.net/latest"
SHIP_CATEGORY_ID = 6
SHIP_TYPES_TTL = 2 * 86400  # ships change rarely; refresh at most every 2 days
EVE_SSO_TOKEN_URL = "https://login.eveonline.com/v2/oauth/token"

JITA_44_STATION_ID = 60003760
FORGE_REGION_ID    = 10000002

# Project price comparison: target = jita_buy_max * MARKET_TARGET_RATIO.
# Flag if |project_price - target| / target > PRICE_DIFF_THRESHOLD.
MARKET_TARGET_RATIO  = float(os.getenv("MARKET_TARGET_RATIO", "0.9"))
PRICE_DIFF_THRESHOLD = float(os.getenv("PRICE_DIFF_THRESHOLD", "0.15"))


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


async def get_character_wallet_journal(character_id: int, access_token: str) -> list[dict]:
    """All wallet-journal entries (ESI keeps ~30 days). Walks pages until empty."""
    result: list[dict] = []
    page = 1
    async with httpx.AsyncClient() as client:
        while True:
            r = await client.get(
                f"{ESI_BASE}/characters/{character_id}/wallet/journal/",
                headers={"Authorization": f"Bearer {access_token}"},
                params={"page": page},
            )
            r.raise_for_status()
            entries = r.json() or []
            if not entries:
                break
            result.extend(entries)
            total_pages = int(r.headers.get("X-Pages", "1"))
            if page >= total_pages:
                break
            page += 1
    return result


async def get_skills(character_id: int, access_token: str) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{ESI_BASE}/characters/{character_id}/skills/",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        r.raise_for_status()
        return r.json()


async def get_character_fleet(character_id: int, access_token: str) -> dict | None:
    """Returns {fleet_id, fleet_boss_id, role, squad_id, wing_id} or None if not in fleet."""
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{ESI_BASE}/characters/{character_id}/fleet/",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()


async def get_fleet_members(fleet_id: int, access_token: str) -> list:
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{ESI_BASE}/fleets/{fleet_id}/members/",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        r.raise_for_status()
        return r.json()


async def get_fleet_info(fleet_id: int, access_token: str) -> dict:
    """Returns {is_free_move, is_registered, is_voice_enabled, motd}."""
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{ESI_BASE}/fleets/{fleet_id}/",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        r.raise_for_status()
        return r.json()


async def get_fleet_wings(fleet_id: int, access_token: str) -> list:
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{ESI_BASE}/fleets/{fleet_id}/wings/",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        r.raise_for_status()
        return r.json()


async def move_fleet_member(fleet_id: int, member_id: int, role: str,
                            wing_id: int | None, squad_id: int | None,
                            access_token: str) -> None:
    body = {"role": role}
    if role in ("wing_commander", "squad_commander", "squad_member") and wing_id is not None:
        body["wing_id"] = wing_id
    if role in ("squad_commander", "squad_member") and squad_id is not None:
        body["squad_id"] = squad_id
    async with httpx.AsyncClient() as client:
        r = await client.put(
            f"{ESI_BASE}/fleets/{fleet_id}/members/{member_id}/",
            headers={"Authorization": f"Bearer {access_token}"},
            json=body,
        )
        r.raise_for_status()


async def get_character_attributes(character_id: int, access_token: str) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{ESI_BASE}/characters/{character_id}/attributes/",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        r.raise_for_status()
        return r.json()


async def get_character_skillqueue(character_id: int, access_token: str) -> list:
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{ESI_BASE}/characters/{character_id}/skillqueue/",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        r.raise_for_status()
        return r.json()


async def get_character_location(character_id: int, access_token: str) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{ESI_BASE}/characters/{character_id}/location/",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        r.raise_for_status()
        return r.json()


async def get_character_online(character_id: int, access_token: str) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{ESI_BASE}/characters/{character_id}/online/",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        r.raise_for_status()
        return r.json()


def _decode_python_literal(s):
    """Unwrap Python-2-style unicode literals like u'\\u271a1' to '✚1'.
    Safe: only acts on strings starting with u' or u" and uses ast.literal_eval."""
    if not isinstance(s, str):
        return s
    if not (s.startswith(("u'", 'u"')) and s.endswith(("'", '"'))):
        return s
    try:
        result = ast.literal_eval(s)
        return result if isinstance(result, str) else s
    except (ValueError, SyntaxError):
        return s


async def get_character_ship(character_id: int, access_token: str) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{ESI_BASE}/characters/{character_id}/ship/",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        r.raise_for_status()
        data = r.json()
        if "ship_name" in data:
            data["ship_name"] = _decode_python_literal(data["ship_name"])
        return data


async def get_character_contracts(character_id: int, access_token: str) -> list:
    result = []
    page = 1
    async with httpx.AsyncClient() as client:
        while True:
            r = await client.get(
                f"{ESI_BASE}/characters/{character_id}/contracts/",
                params={"datasource": "tranquility", "page": page},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            r.raise_for_status()
            result.extend(r.json())
            if page >= int(r.headers.get("X-Pages", 1)):
                break
            page += 1
    return result


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


async def get_corp_contract_items(corporation_id: int, contract_id: int,
                                   access_token: str) -> list:
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{ESI_BASE}/corporations/{corporation_id}/contracts/{contract_id}/items/",
            params={"datasource": "tranquility"},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        r.raise_for_status()
        return r.json()


async def _fetch_ship_type_ids_from_esi() -> set[int]:
    """All ship type_ids: category 6 → its groups → each group's types."""
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{ESI_BASE}/universe/categories/{SHIP_CATEGORY_ID}/")
        r.raise_for_status()
        group_ids = r.json().get("groups", [])
        ids: set[int] = set()
        for gid in group_ids:
            gr = await client.get(f"{ESI_BASE}/universe/groups/{gid}/")
            gr.raise_for_status()
            ids.update(gr.json().get("types", []))
        return ids


async def get_ship_type_ids() -> set[int]:
    """Set of all ship type_ids. Cached in SQLite, refreshed at most every 2
    days. On ESI failure, falls back to the stale cached set (or empty)."""
    cached = await get_ship_types_cache()
    if cached and (time.time() - cached["fetched_at"]) < SHIP_TYPES_TTL:
        return cached["type_ids"]
    try:
        ids = await _fetch_ship_type_ids_from_esi()
    except httpx.HTTPError:
        return cached["type_ids"] if cached else set()
    await set_ship_types_cache(ids)
    return ids


async def get_corp_projects(corporation_id: int, access_token: str) -> list:
    # New ESI endpoint: lives at root (not /latest/), requires X-Compatibility-Date header.
    result = []
    cursor = None
    async with httpx.AsyncClient() as client:
        while True:
            params = {"limit": 100}
            if cursor:
                params["after"] = cursor
            r = await client.get(
                f"https://esi.evetech.net/corporations/{corporation_id}/projects",
                params=params,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "X-Compatibility-Date": "2026-01-01",
                },
            )
            r.raise_for_status()
            data = r.json()
            result.extend(data.get("projects", []))
            cursor = data.get("cursor", {}).get("after")
            if not cursor:
                break
    return result


async def get_corp_project_detail(corporation_id: int, project_id: str, access_token: str) -> dict:
    # New ESI endpoint; same X-Compatibility-Date contract as get_corp_projects.
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"https://esi.evetech.net/corporations/{corporation_id}/projects/{project_id}",
            headers={
                "Authorization": f"Bearer {access_token}",
                "X-Compatibility-Date": "2026-01-01",
            },
        )
        r.raise_for_status()
        return r.json()


async def get_contacts(character_id: int, access_token: str) -> list[dict]:
    """All of the character's contacts (walks X-Pages)."""
    result: list[dict] = []
    page = 1
    async with httpx.AsyncClient() as client:
        while True:
            r = await client.get(
                f"{ESI_BASE}/characters/{character_id}/contacts/",
                headers={"Authorization": f"Bearer {access_token}"},
                params={"page": page},
            )
            r.raise_for_status()
            batch = r.json() or []
            result.extend(batch)
            if page >= int(r.headers.get("X-Pages", "1")):
                break
            page += 1
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


async def _fetch_structure(structure_id: int, access_token: str) -> dict | None:
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(
                f"{ESI_BASE}/universe/structures/{structure_id}/",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            r.raise_for_status()
            data = r.json()
            return {
                "name": data.get("name"),
                "type_id": data.get("type_id"),
                "system_id": data.get("solar_system_id"),
                "owner_id": data.get("owner_id"),
            }
        except httpx.HTTPError:
            return None


async def get_structure_info(structure_id: int, access_token: str) -> dict:
    """Returns {name, type_id, system_id, owner_id} for a citadel.
    Caches successful lookups; old cache entries lacking type_id or owner_id
    are refreshed automatically so the Structures limited-mode view can find
    corp-owned citadels."""
    cached = await get_cached_structure(structure_id)
    if (cached and cached.get("name")
            and cached.get("type_id")
            and cached.get("owner_id")):
        return cached  # Fully resolved.

    info = await _fetch_structure(structure_id, access_token)
    if not info or not info.get("name"):
        for char_id in await list_all_character_ids():
            try:
                other = await get_valid_token(char_id)
            except Exception:
                continue
            if other == access_token:
                continue
            info = await _fetch_structure(structure_id, other)
            if info and info.get("name"):
                break

    if info and info.get("name"):
        await cache_structure_name(
            structure_id, info["name"],
            info.get("type_id"), info.get("system_id"), info.get("owner_id"),
        )
        return info
    if cached and cached.get("name"):
        return cached  # Fetch failed but we have a stale name.
    return {"name": f"Citadel #{str(structure_id)[-5:]}", "type_id": None, "system_id": None}


async def get_station_info(station_id: int) -> dict:
    """NPC station info: name, type_id, system_id. Public ESI."""
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(f"{ESI_BASE}/universe/stations/{station_id}/")
            r.raise_for_status()
            data = r.json()
            return {
                "name": data.get("name", f"Station #{station_id}"),
                "type_id": data.get("type_id"),
                "system_id": data.get("system_id"),
            }
        except httpx.HTTPError:
            return {"name": f"Station #{str(station_id)[-5:]}",
                    "type_id": None, "system_id": None}


async def get_location_info(location_id: int, access_token: str) -> dict:
    """Returns {name, type_id, system_id} for any station or structure."""
    if location_id < 1_000_000_000_000:
        return await get_station_info(location_id)
    return await get_structure_info(location_id, access_token)


async def get_location_name(location_id: int, access_token: str) -> str:
    info = await get_location_info(location_id, access_token)
    return info["name"]


async def get_group_info(group_id: int) -> dict | None:
    """Public ESI; returns {group_id, name, category_id, types: [type_ids], ...} or None."""
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(f"{ESI_BASE}/universe/groups/{group_id}/")
            r.raise_for_status()
            return r.json()
        except httpx.HTTPError:
            return None


async def get_type_info(type_id: int) -> dict:
    """Public ESI; returns {name, group_id}. Cached."""
    cached = await get_cached_type(type_id)
    if cached:
        return cached
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(f"{ESI_BASE}/universe/types/{type_id}/")
            r.raise_for_status()
            data = r.json()
            name = data.get("name", f"Type #{type_id}")
            group_id = data.get("group_id")
            await cache_type(type_id, name, group_id)
            return {"name": name, "group_id": group_id}
        except httpx.HTTPError:
            return {"name": f"Type #{type_id}", "group_id": None}


async def get_system_info(system_id: int) -> dict:
    """Public ESI; returns {name, security_status}. Cached."""
    cached = await get_cached_system(system_id)
    if cached:
        return cached
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(f"{ESI_BASE}/universe/systems/{system_id}/")
            r.raise_for_status()
            data = r.json()
            name = data.get("name", f"System #{system_id}")
            sec = data.get("security_status")
            await cache_system(system_id, name, sec)
            return {"name": name, "security_status": sec}
        except httpx.HTTPError:
            return {"name": f"System #{system_id}", "security_status": None}


async def get_corp_project_contributors(corporation_id: int, project_id: str,
                                        access_token: str) -> list:
    """List of {id, name, contributed} for a project. New ESI; root URL + compat header."""
    result = []
    cursor = None
    async with httpx.AsyncClient() as client:
        while True:
            params = {"limit": 100}
            if cursor:
                params["after"] = cursor
            r = await client.get(
                f"https://esi.evetech.net/corporations/{corporation_id}/projects/{project_id}/contributors",
                params=params,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "X-Compatibility-Date": "2026-01-01",
                },
            )
            r.raise_for_status()
            data = r.json()
            result.extend(data.get("contributors", []))
            cursor = data.get("cursor", {}).get("after")
            if not cursor:
                break
    return result


async def open_contract_in_client(contract_id: int, access_token: str) -> None:
    """Open the contract window in the EVE client of the authenticated character."""
    async with httpx.AsyncClient() as client:
        r = await client.post(
            "https://esi.evetech.net/ui/openwindow/contract",
            params={"contract_id": contract_id},
            headers={
                "Authorization": f"Bearer {access_token}",
                "X-Compatibility-Date": "2026-01-01",
            },
        )
        r.raise_for_status()


async def open_information_in_client(target_id: int, access_token: str) -> None:
    """Open the in-game Show Info window for a character/corporation/alliance in the
    authenticated character's client. (ESI openwindow/information does not support item types.)"""
    async with httpx.AsyncClient() as client:
        r = await client.post(
            "https://esi.evetech.net/ui/openwindow/information",
            params={"target_id": target_id},
            headers={
                "Authorization": f"Bearer {access_token}",
                "X-Compatibility-Date": "2026-01-01",
            },
        )
        r.raise_for_status()


async def open_market_in_client(type_id: int, access_token: str) -> None:
    """Open the in-game Market Details window for a type in the authenticated character's client."""
    async with httpx.AsyncClient() as client:
        r = await client.post(
            "https://esi.evetech.net/ui/openwindow/marketdetails",
            params={"type_id": type_id},
            headers={
                "Authorization": f"Bearer {access_token}",
                "X-Compatibility-Date": "2026-01-01",
            },
        )
        r.raise_for_status()


async def get_server_status() -> dict | None:
    """EVE Tranquility server status. Public ESI, no auth. Returns None on failure."""
    async with httpx.AsyncClient(timeout=4.0) as client:
        try:
            r = await client.get(f"{ESI_BASE}/status/")
            r.raise_for_status()
            return r.json()
        except httpx.HTTPError:
            return None


async def resolve_type_ids(names: list[str]) -> dict[str, int]:
    """Bulk-resolve EVE item names to type_ids via /universe/ids/."""
    clean = list({n for n in names if n})
    if not clean:
        return {}
    async with httpx.AsyncClient() as client:
        try:
            r = await client.post(f"{ESI_BASE}/universe/ids/", json=clean)
            r.raise_for_status()
            data = r.json()
            return {item["name"]: item["id"] for item in data.get("inventory_types", [])}
        except httpx.HTTPError:
            return {}


async def get_jita_buy_max(type_id: int) -> float | None:
    """Highest buy order price in Jita 4-4 for the given type, or None."""
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(
                f"{ESI_BASE}/markets/{FORGE_REGION_ID}/orders/",
                params={"order_type": "buy", "type_id": type_id, "page": 1},
            )
            r.raise_for_status()
            jita = [o["price"] for o in r.json()
                    if o.get("location_id") == JITA_44_STATION_ID]
            return max(jita) if jita else None
        except httpx.HTTPError:
            return None


# ── Mail (read) ──────────────────────────────────────────────────────────────

async def get_mail_headers(character_id: int, access_token: str,
                           labels: list[int] | None = None,
                           last_mail_id: int | None = None) -> list[dict]:
    """Up to 50 mail headers, newest first. `last_mail_id` pages older."""
    params: dict = {}
    if labels:
        params["labels"] = ",".join(str(l) for l in labels)
    if last_mail_id:
        params["last_mail_id"] = last_mail_id
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{ESI_BASE}/characters/{character_id}/mail/",
            headers={"Authorization": f"Bearer {access_token}"},
            params=params,
        )
        r.raise_for_status()
        return r.json() or []


async def get_mail(character_id: int, access_token: str, mail_id: int) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{ESI_BASE}/characters/{character_id}/mail/{mail_id}/",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        r.raise_for_status()
        return r.json()


async def get_mail_labels(character_id: int, access_token: str) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{ESI_BASE}/characters/{character_id}/mail/labels/",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        r.raise_for_status()
        return r.json()


async def get_mailing_lists(character_id: int, access_token: str) -> list[dict]:
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{ESI_BASE}/characters/{character_id}/mail/lists/",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        r.raise_for_status()
        return r.json() or []


# ── Mail (write / organize / send) ───────────────────────────────────────────

async def set_mail(character_id: int, access_token: str, mail_id: int,
                   read: bool | None = None, labels: list[int] | None = None) -> None:
    """Mark read/unread and/or replace the mail's labels."""
    payload: dict = {}
    if read is not None:
        payload["read"] = read
    if labels is not None:
        payload["labels"] = labels
    async with httpx.AsyncClient() as client:
        r = await client.put(
            f"{ESI_BASE}/characters/{character_id}/mail/{mail_id}/",
            headers={"Authorization": f"Bearer {access_token}"},
            json=payload,
        )
        r.raise_for_status()


async def delete_mail(character_id: int, access_token: str, mail_id: int) -> None:
    async with httpx.AsyncClient() as client:
        r = await client.delete(
            f"{ESI_BASE}/characters/{character_id}/mail/{mail_id}/",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        r.raise_for_status()


async def create_mail_label(character_id: int, access_token: str,
                            name: str, color: str) -> int:
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{ESI_BASE}/characters/{character_id}/mail/labels/",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"name": name, "color": color},
        )
        r.raise_for_status()
        return r.json()


async def delete_mail_label(character_id: int, access_token: str, label_id: int) -> None:
    async with httpx.AsyncClient() as client:
        r = await client.delete(
            f"{ESI_BASE}/characters/{character_id}/mail/labels/{label_id}/",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        r.raise_for_status()


async def send_mail(character_id: int, access_token: str, recipients: list[dict],
                    subject: str, body: str, approved_cost: int = 0) -> int:
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{ESI_BASE}/characters/{character_id}/mail/",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"recipients": recipients, "subject": subject,
                  "body": body, "approved_cost": approved_cost},
        )
        r.raise_for_status()
        return r.json()


async def resolve_recipient_ids(names: list[str]) -> list[dict]:
    """Resolve names to mail recipients via /universe/ids/.
    Returns [{recipient_id, recipient_type, name}] for characters/corps/alliances."""
    clean = list({n for n in names if n})
    if not clean:
        return []
    type_map = {
        "characters": "character",
        "corporations": "corporation",
        "alliances": "alliance",
    }
    async with httpx.AsyncClient() as client:
        try:
            r = await client.post(f"{ESI_BASE}/universe/ids/", json=clean)
            r.raise_for_status()
            data = r.json()
        except httpx.HTTPError:
            return []
    out: list[dict] = []
    for key, rtype in type_map.items():
        for item in data.get(key, []):
            out.append({"recipient_id": item["id"],
                        "recipient_type": rtype, "name": item["name"]})
    return out


async def get_jita_sell_min(type_id: int) -> float | None:
    """Lowest sell order price in Jita 4-4 for the given type, or None."""
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(
                f"{ESI_BASE}/markets/{FORGE_REGION_ID}/orders/",
                params={"order_type": "sell", "type_id": type_id, "page": 1},
            )
            r.raise_for_status()
            jita = [o["price"] for o in r.json()
                    if o.get("location_id") == JITA_44_STATION_ID]
            return min(jita) if jita else None
        except httpx.HTTPError:
            return None


async def get_jita_prices(type_id: int) -> dict:
    """{'buy': highest 4-4 buy, 'sell': lowest 4-4 sell}; either may be None."""
    buy, sell = await asyncio.gather(
        get_jita_buy_max(type_id), get_jita_sell_min(type_id))
    return {"buy": buy, "sell": sell}


async def get_market_history(type_id: int) -> list[dict]:
    """Daily Jita market history (The Forge) for the type, oldest first.
    Each record: {date, average, highest, lowest, order_count, volume}.
    Returns [] on any ESI failure (incl. a 404 for an untraded type)."""
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(
                f"{ESI_BASE}/markets/{FORGE_REGION_ID}/history/",
                params={"type_id": type_id},
            )
            r.raise_for_status()
            return sorted(r.json(), key=lambda d: d.get("date", ""))
        except httpx.HTTPError:
            return []


async def get_corp_structures(corporation_id: int, access_token: str) -> list:
    result = []
    page = 1
    async with httpx.AsyncClient() as client:
        while True:
            r = await client.get(
                f"{ESI_BASE}/corporations/{corporation_id}/structures/",
                params={"datasource": "tranquility", "page": page},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            r.raise_for_status()
            result.extend(r.json())
            if page >= int(r.headers.get("X-Pages", 1)):
                break
            page += 1
    return result


async def get_corp_starbases(corporation_id: int, access_token: str) -> list:
    result = []
    page = 1
    async with httpx.AsyncClient() as client:
        while True:
            r = await client.get(
                f"{ESI_BASE}/corporations/{corporation_id}/starbases/",
                params={"datasource": "tranquility", "page": page},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            r.raise_for_status()
            result.extend(r.json())
            if page >= int(r.headers.get("X-Pages", 1)):
                break
            page += 1
    return result


async def get_starbase_detail(corporation_id: int, starbase_id: int,
                              system_id: int, access_token: str) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{ESI_BASE}/corporations/{corporation_id}/starbases/{starbase_id}/",
            params={"datasource": "tranquility", "system_id": system_id},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        r.raise_for_status()
        return r.json()


def parse_fitting_link(href: str) -> tuple[int, list[tuple[int, int]]]:
    """Parse an EVE 'fitting:<shipID>:<typeID>;<qty>:...::' link.
    Returns (ship_type_id, [(type_id, qty), ...]); (0, []) if not a fitting link."""
    if not href.startswith("fitting:"):
        return (0, [])
    parts = href[len("fitting:"):].split(":")
    if not parts or not parts[0].isdigit():
        return (0, [])
    ship_id = int(parts[0])
    items: list[tuple[int, int]] = []
    for field in parts[1:]:
        if not field:
            continue
        tid, _, qty = field.partition(";")
        if tid.isdigit():
            items.append((int(tid), int(qty) if qty.isdigit() else 1))
    return (ship_id, items)


# Module slot dogma effects → EFT slot name.
SLOT_EFFECTS = {11: "low", 13: "mid", 12: "high", 2663: "rig", 3772: "subsystem"}
DRONE_CATEGORY_ID = 18

# Static per-type metadata; cached in-memory for the process.
_fitting_meta_cache: dict[int, dict] = {}


async def get_fitting_type_meta(type_id: int) -> dict:
    """Returns {name, slot, kind} for a fitting item.
    slot ∈ low/mid/high/rig/subsystem/None; kind ∈ module/drone/charge."""
    if type_id in _fitting_meta_cache:
        return _fitting_meta_cache[type_id]
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(f"{ESI_BASE}/universe/types/{type_id}/")
            r.raise_for_status()
            data = r.json()
        except httpx.HTTPError:
            return {"name": f"Type #{type_id}", "slot": None, "kind": "charge"}
    name = data.get("name", f"Type #{type_id}")
    slot = None
    for eff in data.get("dogma_effects", []):
        s = SLOT_EFFECTS.get(eff.get("effect_id"))
        if s:
            slot = s
            break
    if slot:
        meta = {"name": name, "slot": slot, "kind": "module"}
    else:
        group_id = data.get("group_id")
        group = await get_group_info(group_id) if group_id else None
        kind = "drone" if group and group.get("category_id") == DRONE_CATEGORY_ID else "charge"
        meta = {"name": name, "slot": None, "kind": kind}
    _fitting_meta_cache[type_id] = meta
    return meta


def build_eft(ship_name: str, fit_name: str, items: list[dict]) -> str:
    """Render items ([{name, slot, kind, qty}]) as EFT/Pyfa-importable text.
    Fitted modules expand to one line per unit; drones/cargo use 'Name xqty'.
    Non-empty slot sections (low, mid, high, rig, subsystem) then drones then cargo,
    each separated by a blank line."""
    order = ["low", "mid", "high", "rig", "subsystem"]
    slots: dict[str, list[str]] = {k: [] for k in order}
    drones: list[str] = []
    cargo: list[str] = []
    for it in items:
        qty = it.get("qty", 1)
        kind = it.get("kind")
        if kind == "module" and it.get("slot") in slots:
            slots[it["slot"]] += [it["name"]] * qty
        elif kind == "drone":
            drones.append(f"{it['name']} x{qty}")
        else:
            cargo.append(f"{it['name']} x{qty}")
    sections = [
        "\n".join(slots[k]) for k in order if slots[k]
    ]
    if drones:
        sections.append("\n".join(drones))
    if cargo:
        sections.append("\n".join(cargo))
    body = "\n\n".join(sections)
    header = f"[{ship_name}, {fit_name}]"
    return f"{header}\n{body}" if body else header


# ── Character fittings (ESI write API; compatibility-date base, no /latest) ────
_FITTINGS_BASE = "https://esi.evetech.net/characters"
# Must be a date published by GET /meta/compatibility-dates/ (ESI rejects others).
_FIT_COMPAT = "2026-05-19"


def _fit_headers(access_token: str) -> dict:
    return {"Authorization": f"Bearer {access_token}", "X-Compatibility-Date": _FIT_COMPAT}


async def list_character_fittings(character_id: int, access_token: str) -> list[dict]:
    """GET the character's saved fittings (raw ESI list)."""
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{_FITTINGS_BASE}/{character_id}/fittings/",
                             headers=_fit_headers(access_token))
        r.raise_for_status()
        return r.json()


async def create_character_fitting(character_id: int, access_token: str, fitting: dict) -> dict:
    """POST a new fitting; returns {'fitting_id': int}."""
    async with httpx.AsyncClient() as client:
        r = await client.post(f"{_FITTINGS_BASE}/{character_id}/fittings/",
                              json=fitting, headers=_fit_headers(access_token))
        r.raise_for_status()
        return r.json()


async def delete_character_fitting(character_id: int, access_token: str, fitting_id: int) -> None:
    """DELETE one saved fitting."""
    async with httpx.AsyncClient() as client:
        r = await client.delete(f"{_FITTINGS_BASE}/{character_id}/fittings/{fitting_id}/",
                               headers=_fit_headers(access_token))
        r.raise_for_status()


async def resolve_type_names(type_ids) -> dict[int, str]:
    """Resolve many type_ids to {id: name} via the cached per-type lookup.

    Preferred over resolve_names() for item types: the bulk /universe/names/
    endpoint 404s the WHOLE batch if any single id is unresolvable and is capped
    at 1000 ids — both of which a large fittings collection trips. get_type_info
    is DB-cached and per-id, so one bad id degrades only itself. Concurrency is
    bounded so a cold cache won't flood ESI."""
    ids = [t for t in {*type_ids} if t]
    if not ids:
        return {}
    sem = asyncio.Semaphore(20)

    async def one(t: int) -> tuple[int, str]:
        async with sem:
            info = await get_type_info(t)
        return t, info.get("name", f"Type #{t}")

    return dict(await asyncio.gather(*(one(t) for t in ids)))


def flag_to_slot_kind(flag: str) -> tuple[str | None, str]:
    """Map an ESI fitting item flag to (slot, kind) for build_eft.
    slot ∈ high/mid/low/rig/subsystem/None; kind ∈ module/drone/charge."""
    if flag.startswith("HiSlot"):        return ("high", "module")
    if flag.startswith("MedSlot"):       return ("mid", "module")
    if flag.startswith("LoSlot"):        return ("low", "module")
    if flag.startswith("RigSlot"):       return ("rig", "module")
    if flag.startswith("SubSystemSlot"): return ("subsystem", "module")
    if flag == "DroneBay":               return (None, "drone")
    return (None, "charge")              # Cargo / FighterBay / anything else


def eft_from_esi_fitting(fitting: dict, name_map: dict[int, str]) -> str:
    """Render an ESI fitting dict to EFT text. name_map: type_id -> name
    (must cover the ship and every item; missing ids degrade to 'Type #<id>')."""
    items = []
    for it in fitting.get("items", []):
        slot, kind = flag_to_slot_kind(it.get("flag", ""))
        items.append({
            "name": name_map.get(it["type_id"], f"Type #{it['type_id']}"),
            "slot": slot, "kind": kind, "qty": it.get("quantity", 1),
        })
    ship_id = fitting["ship_type_id"]
    ship_name = name_map.get(ship_id, f"Type #{ship_id}")
    return build_eft(ship_name, fitting.get("name", "Fitting"), items)


def _strip_trailing_qty(s: str) -> tuple[str, int]:
    """Split a trailing ' xN' quantity off an EFT line. Returns (name, qty>=1)."""
    m = re.search(r"\sx(\d+)$", s)
    if m:
        return s[:m.start()].strip(), max(1, int(m.group(1)))
    return s, 1


_SLOT_FLAG_PREFIX = {"high": "HiSlot", "mid": "MedSlot", "low": "LoSlot",
                     "rig": "RigSlot", "subsystem": "SubSystemSlot"}
_SLOT_MAX = {"high": 8, "mid": 8, "low": 8, "rig": 3, "subsystem": 5}


async def eft_to_esi_fitting(parsed: dict) -> dict:
    """Build an ESI create payload {name, description, ship_type_id, items} from
    a parse_eft() result. Resolves names → type_ids, derives each module's slot
    from dogma, and assigns sequential ESI slot flags. Loaded charges get their
    module's flag; drones → DroneBay; everything else → Cargo. Slot overflow
    spills to Cargo. Raises ValueError listing any names that cannot be resolved.
    """
    names = {parsed["ship_name"]}
    for ln in parsed["lines"]:
        names.add(ln["name"])
        if ln.get("charge"):
            names.add(ln["charge"])
    id_map = await resolve_type_ids(list(names))
    missing = sorted(n for n in names if n not in id_map)
    if missing:
        raise ValueError("Could not resolve: " + ", ".join(missing))
    counters = {k: 0 for k in _SLOT_FLAG_PREFIX}
    items: list[dict] = []
    for ln in parsed["lines"]:
        tid = id_map[ln["name"]]
        meta = await get_fitting_type_meta(tid)
        slot, kind = meta["slot"], meta["kind"]
        qty = ln.get("qty", 1)
        if kind == "module" and slot in _SLOT_FLAG_PREFIX:
            last_flag = None
            for _ in range(qty):
                idx = counters[slot]
                if idx >= _SLOT_MAX[slot]:
                    items.append({"type_id": tid, "quantity": 1, "flag": "Cargo"})
                    continue
                last_flag = f"{_SLOT_FLAG_PREFIX[slot]}{idx}"
                counters[slot] += 1
                items.append({"type_id": tid, "quantity": 1, "flag": last_flag})
            if ln.get("charge") and last_flag:
                items.append({"type_id": id_map[ln["charge"]],
                              "quantity": 1, "flag": last_flag})
        elif kind == "drone":
            items.append({"type_id": tid, "quantity": qty, "flag": "DroneBay"})
        else:
            items.append({"type_id": tid, "quantity": qty, "flag": "Cargo"})
    return {"name": parsed["fit_name"], "description": "",
            "ship_type_id": id_map[parsed["ship_name"]], "items": items}


def fitting_to_create_payload(fit: dict, name: str) -> dict:
    """Build an ESI create payload that clones `fit` under a new name (<=50 chars).
    Copies only the ESI item fields, ignoring any enrichment (ship_name/eft/name)."""
    return {
        "name": name[:50],
        "description": fit.get("description") or "",
        "ship_type_id": fit["ship_type_id"],
        "items": [{"type_id": it["type_id"], "quantity": it["quantity"], "flag": it["flag"]}
                  for it in fit.get("items", [])],
    }


def next_copy_name(base: str, existing: set[str]) -> str:
    """'{base} copy', else '{base} copy2', '{base} copy3', ... avoiding `existing`. <=50 chars."""
    suffix = " copy"
    candidate = (base[:50 - len(suffix)] + suffix)[:50]
    if candidate not in existing:
        return candidate
    n = 2
    while True:
        suffix_n = f" copy{n}"
        candidate = base[:50 - len(suffix_n)] + suffix_n
        if candidate not in existing:
            return candidate
        n += 1


def parse_eft(text: str) -> dict:
    """Parse EFT text into {ship_name, fit_name, lines:[{name, charge, qty}]}.

    The first non-empty line must be '[Ship Name, Fit Name]'. Subsequent
    non-empty lines are modules/drones/cargo; 'Name, Charge' splits an inline
    loaded charge; a trailing ' xN' sets quantity. Lines beginning with '['
    after the header (e.g. '[Empty High slot]') and blank lines are ignored —
    slot is derived later from dogma, not from EFT line position.
    Raises ValueError if the header is missing or malformed.
    """
    lines_in = (text or "").splitlines()
    header = None
    body_start = 0
    for i, raw in enumerate(lines_in):
        s = raw.strip()
        if not s:
            continue
        if s.startswith("[") and s.endswith("]") and "," in s:
            header = s
            body_start = i + 1
        break  # always stop at the first non-empty line: it's the header or it's invalid
    if header is None:
        raise ValueError("Not a valid EFT fit (missing [Ship, Name] header)")
    ship_name, _, fit_name = header[1:-1].partition(",")
    ship_name = ship_name.strip()
    fit_name = fit_name.strip() or "Imported Fit"
    if not ship_name:
        raise ValueError("Not a valid EFT fit (missing ship name)")
    out = []
    for raw in lines_in[body_start:]:
        s = raw.strip()
        if not s or s.startswith("["):
            continue
        name_part = s
        charge = None
        if "," in s:
            name_part, _, charge_part = s.partition(",")
            name_part = name_part.strip()
            charge_part = charge_part.strip()
            if charge_part:
                charge, _ = _strip_trailing_qty(charge_part)  # drop any qty on the charge
                charge = charge or None
        name_part, qty = _strip_trailing_qty(name_part)
        if not name_part:
            continue
        out.append({"name": name_part, "charge": charge, "qty": qty})
    return {"ship_name": ship_name, "fit_name": fit_name, "lines": out}
