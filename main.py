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
from esi import get_valid_token, get_character, get_wallet, get_skills, get_corp_contracts, get_location_name

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

    # Resolve location names for hauling contracts
    loc_ids = {c.get("start_location_id") for c in hauling} | {c.get("end_location_id") for c in hauling}
    loc_ids.discard(None)
    loc_names = dict(zip(
        loc_ids,
        await asyncio.gather(*[get_location_name(lid, access_token) for lid in loc_ids])
    ))
    for c in hauling:
        c["start_name"] = loc_names.get(c.get("start_location_id"), str(c.get("start_location_id", "")))
        c["end_name"]   = loc_names.get(c.get("end_location_id"),   str(c.get("end_location_id", "")))

    return {"hauling": hauling, "abyssal": abyssal, "others": others}


@app.get("/api/member")
async def member(session: str | None = Cookie(None)):
    character_id = await get_current_character_id(session)
    try:
        access_token = await get_valid_token(character_id)
        char_data, wallet, skills = await asyncio.gather(
            get_character(character_id, access_token),
            get_wallet(character_id, access_token),
            get_skills(character_id, access_token),
        )
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="ESI unavailable")

    training_active = bool(
        skills.get("skills") and any(
            s.get("active_skill_level", 0) < s.get("trained_skill_level", 0)
            for s in skills.get("skills", [])
        )
    )

    return {
        "character_id": character_id,
        "character_name": char_data.get("name"),
        "corporation_id": char_data.get("corporation_id"),
        "security_status": char_data.get("security_status", 0),
        "wallet_balance": wallet,
        "total_sp": skills.get("total_sp", 0),
        "training_active": training_active,
    }


# ── Static files (must be last) ───────────────────────────────────────────────

app.mount("/", StaticFiles(directory="static", html=True), name="static")
