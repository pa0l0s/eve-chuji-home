import os
import time
import secrets
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta

import httpx
from fastapi import FastAPI, Cookie, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

import db as database
from auth import build_login_url, exchange_code, verify_token, make_session_cookie, read_session_cookie
from esi import (
    get_valid_token, get_character, get_wallet, get_skills,
    get_character_location, get_character_online, get_character_ship,
    get_character_contracts,
    get_corp_contracts, get_corp_projects,
    get_corp_structures, get_corp_starbases, get_starbase_detail,
    get_location_name, get_location_info, get_structure_info,
    get_type_info, get_system_info, get_server_status,
    open_contract_in_client,
    resolve_names, resolve_type_ids, get_jita_buy_max,
    MARKET_TARGET_RATIO, PRICE_DIFF_THRESHOLD,
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

@app.post("/api/ui/open-contract/{contract_id}")
async def open_contract(contract_id: int, session: str | None = Cookie(None)):
    """Send an in-game UI open-contract request to the user's EVE client."""
    character_id = await get_current_character_id(session)
    try:
        access_token = await get_valid_token(character_id)
        await open_contract_in_client(contract_id, access_token)
    except httpx.HTTPStatusError as e:
        print(f"ESI open-contract HTTP {e.response.status_code}: {e.response.text}")
        if e.response.status_code == 403:
            raise HTTPException(status_code=403, detail="Missing scope: esi-ui.open_window.v1")
        raise HTTPException(status_code=502, detail="ESI unavailable")
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="ESI unavailable")
    return {"ok": True}


@app.get("/api/status")
async def server_status():
    """Public EVE Tranquility status. No auth required."""
    s = await get_server_status()
    if not s:
        return {"online": False, "players": None, "vip": False}
    return {
        "online": True,
        "players": s.get("players"),
        "vip": bool(s.get("vip", False)),
        "version": s.get("server_version"),
        "start_time": s.get("start_time"),
    }


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

    # Market enrichment is best-effort: a failure here must not break the project list.
    market_error = None
    name_to_type, err = await _safe(resolve_type_ids(
        [p["name"] for p in active if p.get("name")]
    ))
    if err or name_to_type is None:
        market_error = f"Type resolution failed: {err or 'no data'}"
        name_to_type = {}

    buy_maxes: dict[int, float | None] = {}
    needed_ids = list(set(name_to_type.values()))
    if needed_ids:
        # Each Jita lookup wrapped — one failure leaves the others' prices intact.
        results = await asyncio.gather(*[_safe(get_jita_buy_max(t)) for t in needed_ids])
        buy_maxes = {tid: r for tid, (r, _) in zip(needed_ids, results)}

    for p in active:
        try:
            tid = name_to_type.get(p.get("name"))
            buy_max = buy_maxes.get(tid) if tid else None
            desired = (p.get("progress") or {}).get("desired") or 0
            reward = p.get("reward") or {}
            initial = reward.get("initial")
            if not (tid and buy_max and desired and initial):
                continue
            project_price = initial / desired
            target_price  = buy_max * MARKET_TARGET_RATIO
            diff_ratio    = abs(project_price - target_price) / target_price
            p["market"] = {
                "type_id": tid,
                "jita_buy_max": buy_max,
                "target_price": target_price,
                "project_price": project_price,
                "diff_ratio": diff_ratio,
                "needs_update": diff_ratio > PRICE_DIFF_THRESHOLD,
            }
        except Exception as e:
            print(f"Market enrichment failed for project {p.get('name')!r}: {e}")

    return {
        "active": active,
        "closed": closed,
        "market_config": {
            "target_ratio": MARKET_TARGET_RATIO,
            "diff_threshold": PRICE_DIFF_THRESHOLD,
        },
        "market_error": market_error,
    }


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


def _parse_iso(s: str | None):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _classify_contract(c: dict, now: datetime) -> dict:
    """Returns {category, priority, attention_reason, time_left_sec}.
    category ∈ {'critical', 'warning', 'ok', 'done'}; lower priority floats to top."""
    status   = c.get("status")
    expired  = _parse_iso(c.get("date_expired"))
    accepted = _parse_iso(c.get("date_accepted"))
    days     = c.get("days_to_complete") or 0
    deadline = accepted + timedelta(days=days) if (accepted and days) else None

    if status == "failed":
        return {"category": "critical", "priority": 0,
                "attention": "Failed — collateral consequences", "time_left_sec": None}
    if status == "rejected":
        return {"category": "critical", "priority": 4,
                "attention": "Rejected by recipient", "time_left_sec": None}
    if status == "outstanding":
        if expired and expired <= now:
            return {"category": "critical", "priority": 1,
                    "attention": "Expired without acceptance", "time_left_sec": 0}
        if expired:
            secs = (expired - now).total_seconds()
            if secs < 86400:
                return {"category": "warning", "priority": 10,
                        "attention": "Expires in < 24h", "time_left_sec": secs}
            if secs < 172800:
                return {"category": "warning", "priority": 12,
                        "attention": "Expires in < 48h", "time_left_sec": secs}
            return {"category": "ok", "priority": 50,
                    "attention": None, "time_left_sec": secs}
    if status == "in_progress":
        if deadline and deadline <= now:
            return {"category": "critical", "priority": 2,
                    "attention": "Past delivery deadline", "time_left_sec": 0}
        if deadline:
            secs = (deadline - now).total_seconds()
            if secs < 86400:
                return {"category": "warning", "priority": 11,
                        "attention": "Deadline in < 24h", "time_left_sec": secs}
            return {"category": "ok", "priority": 40,
                    "attention": None, "time_left_sec": secs}
        return {"category": "ok", "priority": 45,
                "attention": None, "time_left_sec": None}
    return {"category": "done", "priority": 1000,
            "attention": None, "time_left_sec": None}


async def _safe(coro):
    """Run an ESI coroutine; return (result, error_string). Never raises."""
    try:
        return await coro, None
    except httpx.HTTPStatusError as e:
        msg = f"ESI {e.response.status_code}"
        if e.response.status_code == 403:
            msg = "Missing scope or insufficient role"
        return None, msg
    except httpx.HTTPError as e:
        return None, f"ESI unavailable: {type(e).__name__}"
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def _wrap(data, error):
    return {"data": data, "error": error}


@app.get("/api/member")
async def member(session: str | None = Cookie(None)):
    """Profile data with per-section error isolation. Contracts moved to /api/me/contracts."""
    character_id = await get_current_character_id(session)
    try:
        access_token = await get_valid_token(character_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Token refresh failed: {e}")

    (char_data, char_err), (wallet, wallet_err), (skills, skills_err), \
    (location, loc_err),   (online, online_err),  (ship, ship_err) = await asyncio.gather(
        _safe(get_character(character_id, access_token)),
        _safe(get_wallet(character_id, access_token)),
        _safe(get_skills(character_id, access_token)),
        _safe(get_character_location(character_id, access_token)),
        _safe(get_character_online(character_id, access_token)),
        _safe(get_character_ship(character_id, access_token)),
    )

    profile = None
    if char_data:
        profile = {
            "character_name": char_data.get("name"),
            "corporation_id": char_data.get("corporation_id"),
            "security_status": char_data.get("security_status", 0),
        }

    skills_summary = None
    if skills:
        training_active = bool(
            skills.get("skills") and any(
                s.get("active_skill_level", 0) < s.get("trained_skill_level", 0)
                for s in skills.get("skills", [])
            )
        )
        skills_summary = {"total_sp": skills.get("total_sp", 0), "training_active": training_active}

    location_block = None
    if location:
        sys_info, sys_err = await _safe(get_system_info(location["solar_system_id"])) \
            if location.get("solar_system_id") else (None, None)
        docked, docked_err = None, None
        if location.get("station_id"):
            docked, docked_err = await _safe(get_location_name(location["station_id"], access_token))
        elif location.get("structure_id"):
            info, docked_err = await _safe(get_structure_info(location["structure_id"], access_token))
            docked = info["name"] if info else None
        location_block = {
            "system_id": location.get("solar_system_id"),
            "system_name": (sys_info or {}).get("name"),
            "system_security": (sys_info or {}).get("security_status"),
            "docked_at": docked,
        }

    ship_block = None
    if ship:
        ship_type, _ = await _safe(get_type_info(ship["ship_type_id"])) if ship.get("ship_type_id") else (None, None)
        ship_block = {
            "type_id": ship.get("ship_type_id"),
            "type_name": (ship_type or {}).get("name"),
            "name": ship.get("ship_name"),
        }

    online_block = None
    if online:
        online_block = {
            "is_online": online.get("online", False),
            "last_login": online.get("last_login"),
            "last_logout": online.get("last_logout"),
            "logins": online.get("logins"),
        }

    return {
        "character_id": character_id,
        "profile":  _wrap(profile,         char_err),
        "wallet":   _wrap(wallet,          wallet_err),
        "skills":   _wrap(skills_summary,  skills_err),
        "location": _wrap(location_block,  loc_err),
        "online":   _wrap(online_block,    online_err),
        "ship":     _wrap(ship_block,      ship_err),
    }


@app.get("/api/me/contracts")
async def my_contracts(session: str | None = Cookie(None)):
    character_id = await get_current_character_id(session)
    try:
        access_token = await get_valid_token(character_id)
        contracts_raw = await get_character_contracts(character_id, access_token)
    except httpx.HTTPStatusError as e:
        print(f"ESI character contracts HTTP {e.response.status_code}: {e.response.text}")
        if e.response.status_code == 403:
            raise HTTPException(status_code=403, detail="Missing scope: read_character_contracts")
        raise HTTPException(status_code=502, detail="ESI unavailable")
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="ESI unavailable")

    now = datetime.now(timezone.utc)
    char_ids = []
    for c in contracts_raw:
        c.update(_classify_contract(c, now))
        for k in ("issuer_id", "assignee_id", "acceptor_id"):
            if c.get(k):
                char_ids.append(c[k])
    names = await resolve_names(char_ids) if char_ids else {}
    for c in contracts_raw:
        if c.get("issuer_id"):   c["issuer_name"]   = names.get(c["issuer_id"])
        if c.get("assignee_id"): c["assignee_name"] = names.get(c["assignee_id"])
        if c.get("acceptor_id"): c["acceptor_name"] = names.get(c["acceptor_id"])
    contracts = sorted(contracts_raw, key=lambda c: (c["priority"], _parse_iso(c.get("date_issued")) or now))
    return {"character_id": character_id, "contracts": contracts}


# ── Static files (must be last) ───────────────────────────────────────────────

app.mount("/", StaticFiles(directory="static", html=True), name="static")
