import os
import time
import secrets
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta

import httpx
from fastapi import FastAPI, Cookie, HTTPException, Request, Depends
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

import db as database
from auth import (
    build_login_url, exchange_code, verify_token,
    make_session_cookie, read_session_cookie,
    is_admin, get_holding_corporation_ids,
)
from esi import (
    get_valid_token, get_character, get_wallet, get_skills,
    get_character_attributes, get_character_skillqueue,
    get_character_location, get_character_online, get_character_ship,
    get_character_contracts, get_character_fleet,
    get_fleet_info, get_fleet_members, get_fleet_wings, move_fleet_member,
    get_corp_contracts, get_corp_projects, get_corp_project_contributors,
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
    # Admins are exempt from ban (so they can't lock themselves out).
    if not is_admin(character_id) and await database.is_banned(character_id):
        raise HTTPException(status_code=403, detail="Account banned")
    return character_id


async def require_admin(character_id: int = Depends(get_current_character_id)) -> int:
    if not is_admin(character_id):
        raise HTTPException(status_code=403, detail="Admin access required")
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
    await database.update_last_login(character_id)

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
    await database.update_last_seen(character_id)
    return {
        "character_id": row["character_id"],
        "character_name": row["character_name"],
        "corporation_id": row["corporation_id"],
        "is_admin": is_admin(character_id),
    }


def _build_fleet_tree(members: list, wings: list, names: dict) -> dict:
    """Combine /fleets/{id}/members and /fleets/{id}/wings into a tree."""
    def m_summary(m):
        return {
            "character_id": m["character_id"],
            "character_name": names.get(m["character_id"], f"Char {m['character_id']}"),
            "ship_type_id": m.get("ship_type_id"),
            "solar_system_id": m.get("solar_system_id"),
            "role": m.get("role"),
        }

    fc = next((m for m in members if m.get("role") == "fleet_commander"), None)
    wings_out = []
    for w in wings:
        wc = next((m for m in members
                   if m.get("role") == "wing_commander" and m.get("wing_id") == w["id"]),
                  None)
        squads = []
        for sq in w.get("squads", []):
            sc = next((m for m in members
                       if m.get("role") == "squad_commander"
                       and m.get("wing_id") == w["id"]
                       and m.get("squad_id") == sq["id"]),
                      None)
            sq_members = [m_summary(m) for m in members
                          if m.get("role") == "squad_member"
                          and m.get("wing_id") == w["id"]
                          and m.get("squad_id") == sq["id"]]
            squads.append({
                "id": sq["id"], "name": sq["name"],
                "commander": m_summary(sc) if sc else None,
                "members": sq_members,
            })
        wings_out.append({
            "id": w["id"], "name": w["name"],
            "commander": m_summary(wc) if wc else None,
            "squads": squads,
        })
    return {
        "commander": m_summary(fc) if fc else None,
        "wings": wings_out,
    }


@app.get("/api/fleet/current")
async def fleet_current(session: str | None = Cookie(None)):
    character_id = await get_current_character_id(session)
    access_token = await get_valid_token(character_id)

    try:
        fleet_info = await get_character_fleet(character_id, access_token)
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="ESI unavailable")

    if not fleet_info:
        return {"in_fleet": False}

    is_boss = fleet_info.get("fleet_boss_id") == character_id
    response = {
        "in_fleet": True,
        "fleet_id": fleet_info["fleet_id"],
        "fleet_boss_id": fleet_info.get("fleet_boss_id"),
        "is_boss": is_boss,
        "your_role": fleet_info.get("role"),
        "your_wing_id": fleet_info.get("wing_id"),
        "your_squad_id": fleet_info.get("squad_id"),
    }

    if not is_boss:
        # Only /characters/{id}/fleet/ is accessible to non-boss.
        # Resolve fleet boss character name for the UI.
        boss_id = fleet_info.get("fleet_boss_id")
        if boss_id:
            names = await resolve_names([boss_id])
            response["fleet_boss_name"] = names.get(boss_id)
        return response

    # Boss path: full fleet info, roster, wings, saved layout.
    try:
        members, wings, fleet_meta = await asyncio.gather(
            get_fleet_members(fleet_info["fleet_id"], access_token),
            get_fleet_wings(fleet_info["fleet_id"], access_token),
            get_fleet_info(fleet_info["fleet_id"], access_token),
        )
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="ESI unavailable")

    saved = await database.load_fleet_positions(character_id)
    names = await resolve_names([m["character_id"] for m in members])
    response["tree"]  = _build_fleet_tree(members, wings, names)
    response["saved"] = saved
    response["info"] = {
        **fleet_meta,
        "member_count": len(members),
        "wing_count":   len(wings),
        "saved_count":  len(saved),
    }
    return response


async def _require_fleet_boss(character_id: int) -> tuple[int, str]:
    access_token = await get_valid_token(character_id)
    fleet_info = await get_character_fleet(character_id, access_token)
    if not fleet_info:
        raise HTTPException(status_code=400, detail="Not in a fleet")
    if fleet_info.get("fleet_boss_id") != character_id:
        raise HTTPException(status_code=403, detail="Only the fleet boss can manage saved positions")
    return fleet_info["fleet_id"], access_token


@app.post("/api/fleet/saved/{member_id}")
async def fleet_saved_upsert(member_id: int, body: dict,
                             session: str | None = Cookie(None)):
    character_id = await get_current_character_id(session)
    await _require_fleet_boss(character_id)
    role = body.get("role")
    if role not in ("fleet_commander", "wing_commander", "squad_commander", "squad_member"):
        raise HTTPException(status_code=400, detail="Invalid role")
    wing_name  = body.get("wing_name") or None
    squad_name = body.get("squad_name") or None
    if role in ("wing_commander", "squad_commander", "squad_member") and not wing_name:
        raise HTTPException(status_code=400, detail="wing_name required for this role")
    if role in ("squad_commander", "squad_member") and not squad_name:
        raise HTTPException(status_code=400, detail="squad_name required for this role")
    await database.upsert_fleet_position(
        character_id, member_id, body.get("member_name"),
        wing_name, squad_name, role,
    )
    return {"ok": True}


@app.delete("/api/fleet/saved/{member_id}")
async def fleet_saved_delete(member_id: int, session: str | None = Cookie(None)):
    character_id = await get_current_character_id(session)
    await _require_fleet_boss(character_id)
    await database.delete_fleet_position(character_id, member_id)
    return {"ok": True}


@app.delete("/api/fleet/saved")
async def fleet_saved_clear_all(session: str | None = Cookie(None)):
    character_id = await get_current_character_id(session)
    await _require_fleet_boss(character_id)
    removed = await database.delete_all_fleet_positions(character_id)
    return {"ok": True, "removed": removed}


@app.post("/api/fleet/save")
async def fleet_save(session: str | None = Cookie(None)):
    character_id = await get_current_character_id(session)
    access_token = await get_valid_token(character_id)

    fleet_info = await get_character_fleet(character_id, access_token)
    if not fleet_info:
        raise HTTPException(status_code=400, detail="Not in a fleet")
    if fleet_info.get("fleet_boss_id") != character_id:
        raise HTTPException(status_code=403, detail="Only the fleet boss can save")

    try:
        members, wings = await asyncio.gather(
            get_fleet_members(fleet_info["fleet_id"], access_token),
            get_fleet_wings(fleet_info["fleet_id"], access_token),
        )
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"ESI error: {e}")

    wing_by_id   = {w["id"]: w["name"] for w in wings}
    squad_by_ids = {(w["id"], s["id"]): s["name"]
                    for w in wings for s in w.get("squads", [])}
    names = await resolve_names([m["character_id"] for m in members])

    rows = []
    for m in members:
        rows.append({
            "member_character_id": m["character_id"],
            "member_name":         names.get(m["character_id"]),
            "wing_name":           wing_by_id.get(m.get("wing_id")),
            "squad_name":          squad_by_ids.get((m.get("wing_id"), m.get("squad_id"))),
            "role":                m.get("role"),
        })
    await database.save_fleet_positions(character_id, rows)
    return {"ok": True, "saved": len(rows)}


@app.post("/api/fleet/load")
async def fleet_load(session: str | None = Cookie(None)):
    character_id = await get_current_character_id(session)
    access_token = await get_valid_token(character_id)

    fleet_info = await get_character_fleet(character_id, access_token)
    if not fleet_info:
        raise HTTPException(status_code=400, detail="Not in a fleet")
    if fleet_info.get("fleet_boss_id") != character_id:
        raise HTTPException(status_code=403, detail="Only the fleet boss can load")

    saved = await database.load_fleet_positions(character_id)
    if not saved:
        raise HTTPException(status_code=400, detail="No saved positions to load")

    try:
        members, wings = await asyncio.gather(
            get_fleet_members(fleet_info["fleet_id"], access_token),
            get_fleet_wings(fleet_info["fleet_id"], access_token),
        )
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"ESI error: {e}")

    member_ids = {m["character_id"] for m in members}
    wing_by_name = {w["name"]: w for w in wings}

    moved, skipped = [], []
    for s in saved:
        if s["member_character_id"] not in member_ids:
            skipped.append({"member_id": s["member_character_id"],
                            "name": s.get("member_name"), "reason": "not in fleet"})
            continue
        role = s["role"]
        wing = wing_by_name.get(s.get("wing_name") or "")
        squad = None
        if wing and s.get("squad_name"):
            squad = next((sq for sq in wing.get("squads", [])
                          if sq["name"] == s["squad_name"]), None)
        try:
            await move_fleet_member(
                fleet_info["fleet_id"], s["member_character_id"], role,
                wing["id"] if wing else None,
                squad["id"] if squad else None,
                access_token,
            )
            moved.append({"member_id": s["member_character_id"],
                          "name": s.get("member_name"), "role": role})
        except httpx.HTTPError as e:
            skipped.append({"member_id": s["member_character_id"],
                            "name": s.get("member_name"), "reason": str(e)[:80]})
    return {"ok": True, "moved": moved, "skipped": skipped}


@app.get("/api/admin/users")
async def admin_list_users(_admin: int = Depends(require_admin)):
    return {"users": await database.list_all_users()}


@app.post("/api/admin/users/{character_id}/ban")
async def admin_ban(character_id: int, _admin: int = Depends(require_admin)):
    await database.set_banned(character_id, True)
    return {"ok": True}


@app.post("/api/admin/users/{character_id}/unban")
async def admin_unban(character_id: int, _admin: int = Depends(require_admin)):
    await database.set_banned(character_id, False)
    return {"ok": True}


# Friendly cache names → DB table or sentinel.
_CACHE_MAP = {
    "structures": "structure_cache",
    "types":      "type_cache",
    "systems":    "system_cache",
    "janice":     "janice_cache",
}


@app.get("/api/admin/cache")
async def admin_cache_status(_admin: int = Depends(require_admin)):
    db_counts = await database.cache_counts()
    return {
        "caches": [
            {"name": "structures", "rows": db_counts["structure_cache"], "kind": "db"},
            {"name": "types",      "rows": db_counts["type_cache"],      "kind": "db"},
            {"name": "systems",    "rows": db_counts["system_cache"],    "kind": "db"},
            {"name": "janice",     "rows": db_counts["janice_cache"],    "kind": "db"},
            {"name": "contracts",  "rows": len(_contracts_cache),        "kind": "memory"},
        ],
    }


@app.post("/api/admin/cache/{name}/purge")
async def admin_cache_purge(name: str, _admin: int = Depends(require_admin)):
    if name == "all":
        for table in _CACHE_MAP.values():
            await database.clear_cache_table(table)
        _contracts_cache.clear()
        return {"ok": True, "cleared": "all"}
    if name == "contracts":
        _contracts_cache.clear()
        return {"ok": True, "cleared": "contracts"}
    if name not in _CACHE_MAP:
        raise HTTPException(status_code=400, detail="Unknown cache")
    await database.clear_cache_table(_CACHE_MAP[name])
    return {"ok": True, "cleared": name}


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

    for p in active + closed:
        p["sort_priority"] = _project_priority(p)

    return {
        "active": active,
        "closed": closed,
        "market_config": {
            "target_ratio": MARKET_TARGET_RATIO,
            "diff_threshold": PRICE_DIFF_THRESHOLD,
        },
        "market_error": market_error,
    }


# Special-case priority buckets for the project list (lower number = higher priority).
PROJECT_TRACKING_NAMES = {"Remote Boost Shield", "Armor Remote Repair"}
PROJECT_LOOT_NAMES = {
    "Sleeper Data Library",
    "Ancient Coordinates Database",
    "Neural Network Analyzer",
    "Sleeper Drone AI Nexus",
    "Triglavian Survey Database",
}


def _project_priority(p: dict) -> int:
    name = p.get("name") or ""
    if name in PROJECT_TRACKING_NAMES:
        return 1
    if (p.get("market") or {}).get("needs_update"):
        return 2
    if name in PROJECT_LOOT_NAMES:
        return 3
    return 4


@app.get("/api/projects/{project_id}/contributors")
async def project_contributors(project_id: str, session: str | None = Cookie(None)):
    character_id = await get_current_character_id(session)
    try:
        access_token = await get_valid_token(character_id)
        contributors = await get_corp_project_contributors(CORP_ID, project_id, access_token)
    except httpx.HTTPStatusError as e:
        print(f"ESI project contributors HTTP {e.response.status_code}: {e.response.text}")
        if e.response.status_code == 403:
            raise HTTPException(status_code=403, detail="Insufficient corporation roles")
        raise HTTPException(status_code=502, detail="ESI unavailable")
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="ESI unavailable")
    return {"project_id": project_id, "contributors": contributors}


@app.get("/api/structures")
async def structures(session: str | None = Cookie(None)):
    character_id = await get_current_character_id(session)
    try:
        access_token = await get_valid_token(character_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Token refresh failed: {e}")

    # Try director-only endpoints; fall back to cached + universe data on 403.
    (citadels_full, citadels_err), (starbases, starbases_err) = await asyncio.gather(
        _safe(get_corp_structures(CORP_ID, access_token)),
        _safe(get_corp_starbases(CORP_ID, access_token)),
    )

    limited_mode = citadels_full is None
    if limited_mode:
        # Use cached structures owned by the main corp + any holding corps.
        # Cache fills in over time as anyone resolves a structure via
        # /universe/structures/ (contracts, docked location, etc.).
        owner_ids = [CORP_ID, *get_holding_corporation_ids()]
        cached = await database.list_structures_by_owners(owner_ids)
        citadels_full = cached
    else:
        # Seed cache with full info so non-directors benefit on future loads.
        await asyncio.gather(*[
            database.cache_structure_name(
                c["structure_id"], c["name"],
                c.get("type_id"), c.get("system_id"), CORP_ID,
            )
            for c in citadels_full if c.get("structure_id") and c.get("name")
        ])

    starbases = starbases or []

    type_ids   = {s.get("type_id")   for s in citadels_full + starbases}
    system_ids = {s.get("system_id") for s in citadels_full + starbases}
    type_ids.discard(None); system_ids.discard(None)

    types_list, systems_list = await asyncio.gather(
        asyncio.gather(*[get_type_info(t) for t in type_ids]),
        asyncio.gather(*[get_system_info(s) for s in system_ids]),
    )
    types   = dict(zip(type_ids, types_list))
    systems = dict(zip(system_ids, systems_list))

    for s in citadels_full + starbases:
        t = types.get(s.get("type_id"))
        sy = systems.get(s.get("system_id"))
        if t:
            s["type_name"] = t["name"]
        if sy:
            s["system_name"] = sy["name"]
            s["security_status"] = sy.get("security_status")

    return {
        "citadels": citadels_full,
        "starbases": starbases,
        "limited_mode": limited_mode,
        "starbases_error": starbases_err if limited_mode else None,
    }


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
    (location, loc_err),   (online, online_err),  (ship, ship_err), \
    (attributes, attr_err), (skillqueue, sq_err) = await asyncio.gather(
        _safe(get_character(character_id, access_token)),
        _safe(get_wallet(character_id, access_token)),
        _safe(get_skills(character_id, access_token)),
        _safe(get_character_location(character_id, access_token)),
        _safe(get_character_online(character_id, access_token)),
        _safe(get_character_ship(character_id, access_token)),
        _safe(get_character_attributes(character_id, access_token)),
        _safe(get_character_skillqueue(character_id, access_token)),
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

    # Enrich skill queue with skill names; sort by queue_position.
    skillqueue_block = None
    if skillqueue is not None:
        sq_sorted = sorted(skillqueue, key=lambda s: s.get("queue_position", 999))
        skill_ids = list({s["skill_id"] for s in sq_sorted if s.get("skill_id")})
        names_map = {}
        if skill_ids:
            type_infos = await asyncio.gather(*[_safe(get_type_info(t)) for t in skill_ids])
            names_map = {sid: (info or {}).get("name") for sid, (info, _) in zip(skill_ids, type_infos)}
        skillqueue_block = [{**s, "skill_name": names_map.get(s.get("skill_id"))} for s in sq_sorted]

    return {
        "character_id": character_id,
        "profile":    _wrap(profile,         char_err),
        "wallet":     _wrap(wallet,          wallet_err),
        "skills":     _wrap(skills_summary,  skills_err),
        "location":   _wrap(location_block,  loc_err),
        "online":     _wrap(online_block,    online_err),
        "ship":       _wrap(ship_block,      ship_err),
        "attributes": _wrap(attributes,      attr_err),
        "skillqueue": _wrap(skillqueue_block, sq_err),
    }


@app.get("/api/me/wallet")
async def my_wallet(session: str | None = Cookie(None)):
    character_id = await get_current_character_id(session)
    try:
        access_token = await get_valid_token(character_id)
        balance = await get_wallet(character_id, access_token)
        return {"balance": balance}
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 403:
            raise HTTPException(status_code=403, detail="Missing scope: read_character_wallet")
        raise HTTPException(status_code=502, detail="ESI unavailable")
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="ESI unavailable")


# Per-character in-memory cache for personal contracts. Short TTL so the
# Member page feels instant on repeated visits while still picking up new
# contracts within roughly a minute.
_contracts_cache: dict[int, tuple[float, dict]] = {}
CONTRACTS_CACHE_TTL = 60  # seconds


@app.get("/api/me/contracts")
async def my_contracts(session: str | None = Cookie(None)):
    character_id = await get_current_character_id(session)

    cached = _contracts_cache.get(character_id)
    if cached and (time.time() - cached[0]) < CONTRACTS_CACHE_TTL:
        return cached[1]

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

    result = {"character_id": character_id, "contracts": contracts}
    _contracts_cache[character_id] = (time.time(), result)
    return result


# ── Static files (must be last) ───────────────────────────────────────────────

app.mount("/", StaticFiles(directory="static", html=True), name="static")
