import os
import time
import secrets
import asyncio
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Cookie, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

import db as database
from auth import build_login_url, exchange_code, verify_token, make_session_cookie, read_session_cookie
from esi import (
    get_valid_token, get_character, get_wallet, get_skills,
    get_character_location, get_character_online, get_character_ship,
    get_corp_contracts, get_corp_projects,
    get_corp_structures, get_corp_starbases, get_starbase_detail,
    get_location_name, get_location_info, get_structure_info,
    get_type_info, get_system_info,
    resolve_names,
)
from db import cache_structure_name

_corp_id = os.getenv("CORP_ID")
if not _corp_id:
    raise RuntimeError("CORP_ID environment variable is not set")
CORP_ID = int(_corp_id)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await database.init_db()
    yield


app = FastAPI(lifespan=lifespan)


async def get_current_character_id(session: str | None = Cookie(None)) -> int:
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    character_id = read_session_cookie(session)
    if not character_id:
        raise HTTPException(status_code=401, detail="Invalid session")
    return character_id


# ── Auth routes ──────────────────────────────────────────────────────────────

@app.get("/api/auth/login")
async def login():
    state = secrets.token_urlsafe(16)
    url = build_login_url(state)
    r = RedirectResponse(url=url)
    r.set_cookie("oauth_state", state, httponly=True, samesite="lax", max_age=300)
    return r


@app.get("/api/auth/callback")
async def callback(code: str, state: str, request: Request):
    stored_state = request.cookies.get("oauth_state")
    if not stored_state or stored_state != state:
        raise HTTPException(status_code=400, detail="Invalid OAuth state")

    try:
        tokens = await exchange_code(code)
        char_info = verify_token(tokens["access_token"])
    except httpx.HTTPStatusError as e:
        print(f"EVE SSO HTTP {e.response.status_code}: {e.response.text}")
        raise HTTPException(status_code=502, detail="EVE SSO error")
    except httpx.HTTPError as e:
        print(f"EVE SSO connection error: {e}")
        raise HTTPException(status_code=502, detail="EVE SSO error")

    character_id = char_info["CharacterID"]
    character_name = char_info["CharacterName"]

    try:
        esi_char = await get_character(character_id, tokens["access_token"])
        corporation_id = esi_char.get("corporation_id", 0)
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="ESI error")

    if corporation_id != CORP_ID:
        raise HTTPException(status_code=403, detail="Not a member of Grupa Operacyjna ZLY CHUJI")

    await database.upsert_token(
        character_id=character_id,
        character_name=character_name,
        access_token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
        expires_at=time.time() + tokens.get("expires_in", 1200),
        corporation_id=corporation_id,
    )

    resp = RedirectResponse(url="/")
    resp.set_cookie("session", make_session_cookie(character_id), httponly=True, samesite="lax")
    resp.delete_cookie("oauth_state")
    return resp


@app.get("/api/auth/me")
async def me(session: str | None = Cookie(None)):
    character_id = await get_current_character_id(session)
    row = await database.get_token(character_id)
    if not row:
        raise HTTPException(status_code=401, detail="Session expired")
    return {
        "character_id": row["character_id"],
        "character_name": row["character_name"],
        "corporation_id": row["corporation_id"],
    }


@app.get("/api/auth/logout")
async def logout():
    resp = RedirectResponse(url="/")
    resp.delete_cookie("session")
    return resp


# ── Data routes ──────────────────────────────────────────────────────────────

@app.get("/api/contracts")
async def contracts(session: str | None = Cookie(None)):
    character_id = await get_current_character_id(session)
    try:
        access_token = await get_valid_token(character_id)
        raw = await get_corp_contracts(CORP_ID, access_token)
    except httpx.HTTPStatusError as e:
        print(f"ESI contracts HTTP {e.response.status_code}: {e.response.text}")
        if e.response.status_code == 403:
            raise HTTPException(status_code=403, detail="Insufficient corporation roles")
        raise HTTPException(status_code=502, detail="ESI unavailable")
    except httpx.HTTPError as e:
        print(f"ESI contracts connection error: {e}")
        raise HTTPException(status_code=502, detail="ESI unavailable")

    active = [c for c in raw if c.get("status") in ("outstanding", "in_progress")]

    hauling = [c for c in active if c.get("type") == "courier"]
    abyssal = [c for c in active if c.get("type") == "auction"]
    others  = [c for c in active if c.get("type") not in ("courier", "auction")]

    # Resolve location info for hauling and character names for issuer/acceptor.
    loc_ids = {c.get("start_location_id") for c in hauling} | {c.get("end_location_id") for c in hauling}
    loc_ids.discard(None)
    char_ids = [c.get(k) for c in active for k in ("issuer_id", "acceptor_id") if c.get(k)]

    loc_info_list, char_names = await asyncio.gather(
        asyncio.gather(*[get_location_info(lid, access_token) for lid in loc_ids]),
        resolve_names(char_ids),
    )
    loc_data = dict(zip(loc_ids, loc_info_list))

    type_ids   = {info.get("type_id")   for info in loc_data.values() if info.get("type_id")}
    system_ids = {info.get("system_id") for info in loc_data.values() if info.get("system_id")}
    type_infos, system_infos = await asyncio.gather(
        asyncio.gather(*[get_type_info(t) for t in type_ids]),
        asyncio.gather(*[get_system_info(s) for s in system_ids]),
    )
    type_names    = {tid: info["name"] for tid, info in zip(type_ids, type_infos)}
    system_names  = {sid: info["name"] for sid, info in zip(system_ids, system_infos)}

    def _enrich(c, key):
        sid = c.get(f"{key}_location_id")
        info = loc_data.get(sid) or {}
        c[f"{key}_name"]        = info.get("name") or str(sid or "")
        c[f"{key}_type"]        = type_names.get(info.get("type_id"))
        c[f"{key}_system_id"]   = info.get("system_id")
        c[f"{key}_system_name"] = system_names.get(info.get("system_id"))

    for c in hauling:
        _enrich(c, "start")
        _enrich(c, "end")
    for c in active:
        if c.get("issuer_id"):
            c["issuer_name"] = char_names.get(c["issuer_id"])
        if c.get("acceptor_id"):
            c["acceptor_name"] = char_names.get(c["acceptor_id"])

    return {"hauling": hauling, "abyssal": abyssal, "others": others}


@app.get("/api/projects")
async def projects(session: str | None = Cookie(None)):
    character_id = await get_current_character_id(session)
    try:
        access_token = await get_valid_token(character_id)
        raw = await get_corp_projects(CORP_ID, access_token)
    except httpx.HTTPStatusError as e:
        print(f"ESI projects HTTP {e.response.status_code}: {e.response.text}")
        if e.response.status_code == 403:
            raise HTTPException(status_code=403, detail="Insufficient corporation roles")
        raise HTTPException(status_code=502, detail="ESI unavailable")
    except httpx.HTTPError as e:
        print(f"ESI projects connection error: {e}")
        raise HTTPException(status_code=502, detail="ESI unavailable")

    active = [p for p in raw if p.get("state") == "Active"]
    closed = [p for p in raw if p.get("state") in ("Completed", "Closed", "Expired")]
    return {"active": active, "closed": closed}


@app.get("/api/structures")
async def structures(session: str | None = Cookie(None)):
    character_id = await get_current_character_id(session)
    try:
        access_token = await get_valid_token(character_id)
        citadels, starbases = await asyncio.gather(
            get_corp_structures(CORP_ID, access_token),
            get_corp_starbases(CORP_ID, access_token),
        )
    except httpx.HTTPStatusError as e:
        print(f"ESI structures HTTP {e.response.status_code}: {e.response.text}")
        if e.response.status_code == 403:
            raise HTTPException(status_code=403, detail="Insufficient corporation roles")
        raise HTTPException(status_code=502, detail="ESI unavailable")
    except httpx.HTTPError as e:
        print(f"ESI structures connection error: {e}")
        raise HTTPException(status_code=502, detail="ESI unavailable")

    type_ids   = {s.get("type_id") for s in citadels} | {s.get("type_id") for s in starbases}
    system_ids = {s.get("system_id") for s in citadels} | {s.get("system_id") for s in starbases}
    type_ids.discard(None)
    system_ids.discard(None)

    types_list, systems_list = await asyncio.gather(
        asyncio.gather(*[get_type_info(t) for t in type_ids]),
        asyncio.gather(*[get_system_info(s) for s in system_ids]),
    )
    types   = dict(zip(type_ids, types_list))
    systems = dict(zip(system_ids, systems_list))

    for s in citadels + starbases:
        t = types.get(s.get("type_id"))
        sy = systems.get(s.get("system_id"))
        if t:
            s["type_name"] = t["name"]
        if sy:
            s["system_name"] = sy["name"]
            s["security_status"] = sy.get("security_status")

    # Seed structure_cache from corp citadel listing so non-directors viewing
    # contracts get full names/types without needing docking rights.
    await asyncio.gather(*[
        cache_structure_name(c["structure_id"], c["name"], c.get("type_id"), c.get("system_id"))
        for c in citadels if c.get("structure_id") and c.get("name")
    ])

    return {"citadels": citadels, "starbases": starbases}


@app.get("/api/starbases/{starbase_id}")
async def starbase_detail(starbase_id: int, system_id: int,
                          session: str | None = Cookie(None)):
    character_id = await get_current_character_id(session)
    try:
        access_token = await get_valid_token(character_id)
        detail = await get_starbase_detail(CORP_ID, starbase_id, system_id, access_token)
    except httpx.HTTPStatusError as e:
        print(f"ESI starbase detail HTTP {e.response.status_code}: {e.response.text}")
        if e.response.status_code == 403:
            raise HTTPException(status_code=403, detail="Insufficient corporation roles")
        raise HTTPException(status_code=502, detail="ESI unavailable")
    except httpx.HTTPError as e:
        print(f"ESI starbase detail connection error: {e}")
        raise HTTPException(status_code=502, detail="ESI unavailable")

    fuel_type_ids = [f.get("type_id") for f in detail.get("fuels") or [] if f.get("type_id")]
    if fuel_type_ids:
        fuel_types_list = await asyncio.gather(*[get_type_info(t) for t in fuel_type_ids])
        fuel_types = dict(zip(fuel_type_ids, fuel_types_list))
        for f in detail["fuels"]:
            t = fuel_types.get(f.get("type_id"))
            if t:
                f["type_name"] = t["name"]
    return detail


@app.get("/api/member")
async def member(session: str | None = Cookie(None)):
    character_id = await get_current_character_id(session)
    try:
        access_token = await get_valid_token(character_id)
        char_data, wallet, skills, location, online, ship = await asyncio.gather(
            get_character(character_id, access_token),
            get_wallet(character_id, access_token),
            get_skills(character_id, access_token),
            get_character_location(character_id, access_token),
            get_character_online(character_id, access_token),
            get_character_ship(character_id, access_token),
        )
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="ESI unavailable")

    training_active = bool(
        skills.get("skills") and any(
            s.get("active_skill_level", 0) < s.get("trained_skill_level", 0)
            for s in skills.get("skills", [])
        )
    )

    system_info = await get_system_info(location["solar_system_id"]) if location.get("solar_system_id") else {}
    docked_name = None
    if location.get("station_id"):
        docked_name = await get_location_name(location["station_id"], access_token)
    elif location.get("structure_id"):
        info = await get_structure_info(location["structure_id"], access_token)
        docked_name = info["name"]

    ship_type = await get_type_info(ship["ship_type_id"]) if ship.get("ship_type_id") else {}

    return {
        "character_id": character_id,
        "character_name": char_data.get("name"),
        "corporation_id": char_data.get("corporation_id"),
        "security_status": char_data.get("security_status", 0),
        "wallet_balance": wallet,
        "total_sp": skills.get("total_sp", 0),
        "training_active": training_active,
        "location": {
            "system_id": location.get("solar_system_id"),
            "system_name": system_info.get("name"),
            "system_security": system_info.get("security_status"),
            "docked_at": docked_name,
        },
        "online": {
            "is_online": online.get("online", False),
            "last_login": online.get("last_login"),
            "last_logout": online.get("last_logout"),
            "logins": online.get("logins"),
        },
        "ship": {
            "type_id": ship.get("ship_type_id"),
            "type_name": ship_type.get("name"),
            "name": ship.get("ship_name"),
        },
    }


# ── Static files (must be last) ───────────────────────────────────────────────

app.mount("/", StaticFiles(directory="static", html=True), name="static")
