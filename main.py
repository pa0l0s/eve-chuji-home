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
    get_valid_token, get_character, get_wallet, get_character_wallet_journal, get_skills,
    get_character_attributes, get_character_skillqueue,
    get_character_location, get_character_online, get_character_ship,
    get_character_contracts, get_character_fleet,
    get_fleet_info, get_fleet_members, get_fleet_wings, move_fleet_member,
    get_corp_contracts, get_corp_contract_items, get_ship_type_ids,
    get_jita_prices, get_market_history,
    get_corp_projects, get_corp_project_detail, get_corp_project_contributors,
    get_corp_structures, get_corp_starbases, get_starbase_detail,
    get_location_name, get_location_info, get_structure_info,
    get_type_info, get_group_info, get_system_info, get_server_status,
    open_contract_in_client, open_information_in_client, open_market_in_client,
    resolve_names, resolve_type_ids, resolve_type_names, get_jita_buy_max,
    MARKET_TARGET_RATIO, PRICE_DIFF_THRESHOLD,
    get_mail_headers, get_mail, get_mail_labels, get_mailing_lists,
    set_mail, delete_mail, create_mail_label, delete_mail_label,
    send_mail, resolve_recipient_ids, get_contacts,
    parse_fitting_link, get_fitting_type_meta, build_eft,
    list_character_fittings, create_character_fitting, delete_character_fitting,
    eft_from_esi_fitting, parse_eft, eft_to_esi_fitting,
    fitting_to_create_payload, next_copy_name,
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

BACKGROUNDS_DIR = os.path.join(os.path.dirname(__file__), "static", "backgrounds")
_BG_EXTS = (".jpg", ".jpeg", ".png", ".webp")


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


@app.post("/api/ui/open-information/{target_id}")
async def open_information(target_id: int, session: str | None = Cookie(None)):
    """Open the in-game Show Info window for a character/corporation/alliance in the user's EVE client."""
    character_id = await get_current_character_id(session)
    try:
        access_token = await get_valid_token(character_id)
        await open_information_in_client(target_id, access_token)
    except httpx.HTTPStatusError as e:
        print(f"ESI open-information HTTP {e.response.status_code}: {e.response.text}")
        if e.response.status_code == 403:
            raise HTTPException(status_code=403, detail="Missing scope: esi-ui.open_window.v1")
        raise HTTPException(status_code=502, detail="ESI unavailable")
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="ESI unavailable")
    return {"ok": True}


@app.post("/api/ui/open-market/{type_id}")
async def open_market(type_id: int, session: str | None = Cookie(None)):
    """Open the in-game Market Details window for a type in the user's EVE client."""
    character_id = await get_current_character_id(session)
    try:
        access_token = await get_valid_token(character_id)
        await open_market_in_client(type_id, access_token)
    except httpx.HTTPStatusError as e:
        print(f"ESI open-market HTTP {e.response.status_code}: {e.response.text}")
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


@app.get("/api/backgrounds")
async def list_backgrounds():
    """Return the sorted list of available background image filenames."""
    try:
        names = os.listdir(BACKGROUNDS_DIR)
    except FileNotFoundError:
        return []
    return sorted(n for n in names if n.lower().endswith(_BG_EXTS))


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
    for w in sorted(wings, key=lambda w: w["id"]):
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


@app.get("/api/admin/projects/settings")
async def admin_project_settings(_admin: int = Depends(require_admin)):
    return {"projects": await database.list_project_settings()}


@app.post("/api/admin/projects/settings")
async def admin_set_project_setting(body: dict, _admin: int = Depends(require_admin)):
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name required")
    if "excluded" in body:
        await database.set_project_price_excluded(name, bool(body["excluded"]))
    if "special" in body:
        await database.set_project_special(name, bool(body["special"]))
    if "track" in body:
        await database.set_project_track(name, bool(body["track"]))
    return {"ok": True}


@app.delete("/api/admin/projects/settings/{name}")
async def admin_delete_project_setting(name: str, _admin: int = Depends(require_admin)):
    await database.delete_project_setting(name)
    return {"ok": True}


@app.get("/api/admin/projects/available")
async def admin_available_projects(character_id: int = Depends(require_admin)):
    """Returns the corp's ESI project names for the admin autocomplete.
    Best-effort: any ESI failure returns 200 with an empty list and a generic
    error field, so the admin panel never breaks on transient network issues."""
    try:
        access_token = await get_valid_token(character_id)
        raw = await get_corp_projects(CORP_ID, access_token)
    except httpx.HTTPStatusError as e:
        print(f"ESI admin/available HTTP {e.response.status_code}: {e.response.text}")
        return {"names": [], "error": "ESI unavailable"}
    except (httpx.HTTPError, ValueError) as e:
        print(f"ESI admin/available error: {e}")
        return {"names": [], "error": "ESI unavailable"}
    names = sorted({p["name"] for p in raw if p.get("name")})
    return {"names": names}


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

    await _enrich_contract_icons(others + abyssal, CORP_ID, access_token)
    ships, others = _split_ship_contracts(others)

    return {"ships": ships, "hauling": hauling,
            "abyssal": abyssal, "others": others}


@app.get("/api/contracts/{contract_id}/detail")
async def contract_detail(contract_id: int, session: str | None = Cookie(None)):
    """Items of a contract with Jita 4-4 buy/sell and totals, for the popup.
    Best-effort: enrichment failures degrade to nulls, never an error."""
    character_id = await get_current_character_id(session)
    access_token = await get_valid_token(character_id)

    if contract_id in _contract_items_cache:
        items = _contract_items_cache[contract_id]
    else:
        items, _ = await _safe(
            get_corp_contract_items(CORP_ID, contract_id, access_token))
        if items is not None:
            _contract_items_cache[contract_id] = items
    if items is None:
        items = []

    included_type_ids = {it.get("type_id") for it in items
                         if it.get("is_included")}
    included_type_ids.discard(None)

    ship_type_ids, type_infos, price_list = await asyncio.gather(
        get_ship_type_ids(),
        asyncio.gather(*[get_type_info(t) for t in included_type_ids]),
        asyncio.gather(*[_cached_jita_prices(t) for t in included_type_ids]),
    )
    names  = {t: info.get("name") for t, info in zip(included_type_ids, type_infos)}
    prices = dict(zip(included_type_ids, price_list))

    return _build_contract_detail(items, names, prices, ship_type_ids)


# Project contributors change slowly; cache per project for 10 min.
_contributors_cache: dict[str, tuple[float, list]] = {}
CONTRIBUTORS_TTL = 600


async def _cached_project_contributors(project_id: str, access_token: str) -> list:
    cached = _contributors_cache.get(project_id)
    if cached and (time.time() - cached[0]) < CONTRIBUTORS_TTL:
        return cached[1]
    contribs = await get_corp_project_contributors(CORP_ID, project_id, access_token)
    _contributors_cache[project_id] = (time.time(), contribs)
    return contribs


_pvp_income_cache: dict[int, tuple[float, float]] = {}  # cid -> (cached_at, amount)
_PVP_INCOME_TTL = 300.0


def _is_pvp_entry(e: dict) -> bool:
    return (
        e.get("ref_type") == "corporation_account_withdrawal"
        and "Cerberus Agnon" in (e.get("description") or "")
    )


def _sum_pvp_income(journal: list[dict]) -> float:
    return sum(e.get("amount", 0) for e in journal if _is_pvp_entry(e))


async def _cached_pvp_income(character_id: int, access_token: str) -> float:
    now = time.time()
    hit = _pvp_income_cache.get(character_id)
    if hit and (now - hit[0]) < _PVP_INCOME_TTL:
        return hit[1]
    journal = await get_character_wallet_journal(character_id, access_token)
    matches = [e for e in journal if _is_pvp_entry(e)]
    if matches:
        await database.upsert_pvp_entries_batch(character_id, matches)
    amount = sum(e.get("amount", 0) for e in matches)
    _pvp_income_cache[character_id] = (now, amount)
    return amount


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

    excluded_names = await database.list_excluded_projects()
    special_names = await database.list_special_projects()
    tracked_names = await database.list_tracked_projects()
    for p in active + closed:
        _compute_project_warn(p, excluded_names)
        p["sort_priority"] = _project_priority(p, tracked_names)
        p["special"] = (p.get("name") or "") in special_names

    # Started-date enrichment: pull `created` from a per-project cache, lazily
    # filling the cache from the ESI detail endpoint for any project we haven't
    # seen yet. `created` is immutable per project, so once cached we never
    # refetch. Best-effort — a failed detail fetch just leaves p["created"] off.
    created_map = await database.get_cached_project_created_map()
    missing = [
        p["id"] for p in active + closed
        if p.get("id") and p["id"] not in created_map
    ]
    if missing:
        details = await asyncio.gather(*[
            _safe(get_corp_project_detail(CORP_ID, pid, access_token))
            for pid in missing
        ])
        new_rows: list[tuple[str, str]] = []
        for pid, (detail, _err) in zip(missing, details):
            if detail:
                c = (detail.get("details") or {}).get("created")
                if c:
                    created_map[pid] = c
                    new_rows.append((pid, c))
        if new_rows:
            await database.cache_project_created_batch(new_rows)
    for p in active + closed:
        pid = p.get("id")
        if pid and pid in created_map:
            p["created"] = created_map[pid]

    # Special projects show their top 3 contributors on the card — best-effort,
    # so a contributor-fetch failure never breaks the projects list.
    special_projects = [p for p in active + closed if p["special"]]
    if special_projects:
        contrib_results = await asyncio.gather(*[
            _safe(_cached_project_contributors(p["id"], access_token))
            for p in special_projects
        ])
        for p, (contribs, _err) in zip(special_projects, contrib_results):
            if contribs:
                top = sorted(contribs, key=lambda c: c.get("contributed") or 0,
                             reverse=True)[:3]
                p["top_contributors"] = [
                    {"id": c.get("id"), "name": c.get("name") or ""} for c in top
                ]

    return {
        "active": active,
        "closed": closed,
        "market_config": {
            "target_ratio": MARKET_TARGET_RATIO,
            "diff_threshold": PRICE_DIFF_THRESHOLD,
        },
        "market_error": market_error,
    }


# EVE skill group IDs (inventory category 16) used by the Member page radar chart.
SKILL_GROUP_IDS = [
    255, 256, 257, 258, 266, 268, 269, 270, 272, 273, 274, 275, 278,
    1209, 1210, 1213, 1216, 1217, 1218, 1240, 1241, 1545, 1570,
]
_skill_group_cache: dict | None = None


async def _ensure_skill_group_map() -> dict:
    """One-time fetch of skill→group mappings via /universe/groups/{id}/."""
    global _skill_group_cache
    if _skill_group_cache is not None:
        return _skill_group_cache
    results = await asyncio.gather(*[get_group_info(gid) for gid in SKILL_GROUP_IDS])
    skill_to_group: dict[int, int] = {}
    group_names:    dict[int, str] = {}
    group_counts:   dict[int, int] = {}
    for gid, group in zip(SKILL_GROUP_IDS, results):
        if not group:
            continue
        group_names[gid]  = group.get("name", f"Group {gid}")
        type_ids = group.get("types", []) or []
        group_counts[gid] = len(type_ids)
        for tid in type_ids:
            skill_to_group[tid] = gid
    _skill_group_cache = {
        "skill_to_group": skill_to_group,
        "group_names":    group_names,
        "group_counts":   group_counts,
    }
    return _skill_group_cache


async def _build_skill_coverage(skills_data: dict) -> list[dict]:
    """Returns per-group coverage list: name, coverage_pct, trained_count, total, sp."""
    if not skills_data:
        return []
    g = await _ensure_skill_group_map()
    per_levels  = {gid: 0 for gid in SKILL_GROUP_IDS}
    per_trained = {gid: 0 for gid in SKILL_GROUP_IDS}
    per_sp      = {gid: 0 for gid in SKILL_GROUP_IDS}
    for s in skills_data.get("skills", []):
        gid = g["skill_to_group"].get(s.get("skill_id"))
        if gid is None:
            continue
        per_levels[gid]  += s.get("active_skill_level", 0)
        per_trained[gid] += 1
        per_sp[gid]      += s.get("skillpoints_in_skill", 0)
    out = []
    for gid in SKILL_GROUP_IDS:
        total = g["group_counts"].get(gid, 0)
        if total == 0:
            continue
        coverage_pct = (per_levels[gid] / (5 * total)) * 100
        out.append({
            "group_id":      gid,
            "group_name":    g["group_names"].get(gid, f"Group {gid}"),
            "coverage_pct":  round(coverage_pct, 1),
            "trained_count": per_trained[gid],
            "total_count":   total,
            "total_sp":      per_sp[gid],
        })
    return out


# Special-case priority buckets for the project list (lower number = higher priority).
# The Track set is now DB-backed (project_settings.track); see list_tracked_projects().
PROJECT_LOOT_NAMES = {
    "Sleeper Data Library",
    "Ancient Coordinates Database",
    "Neural Network Analyzer",
    "Sleeper Drone AI Nexus",
    "Triglavian Survey Database",
}

# Warn-level thresholds:
PROJECT_COMPLETION_RED      = 0.90   # ≥90% done → red frame, high priority
PROJECT_COMPLETION_YELLOW   = 0.75   # ≥75% done → yellow frame
PROJECT_PRICE_YELLOW_RATIO  = 0.075  # >7.5% off Jita target → yellow frame
# (PRICE_DIFF_THRESHOLD imported from esi.py, default 0.15 → red)


def _compute_project_warn(p: dict, price_excluded_names: set[str]) -> None:
    """Assigns p['warn_level'] ∈ {'red','yellow',None}, p['warn_reasons'],
    and p['price_excluded'] (bool)."""
    reasons: list[str] = []
    is_red = is_yellow = False

    progress = p.get("progress") or {}
    desired = progress.get("desired") or 0
    current = progress.get("current") or 0
    if desired > 0:
        pct = current / desired
        if pct >= PROJECT_COMPLETION_RED:
            is_red = True
            reasons.append(f"{pct * 100:.1f}% complete")
        elif pct >= PROJECT_COMPLETION_YELLOW:
            is_yellow = True
            reasons.append(f"{pct * 100:.1f}% complete")

    price_excluded = (p.get("name") or "") in price_excluded_names
    p["price_excluded"] = price_excluded

    market = p.get("market")
    if market and market.get("diff_ratio") is not None and not price_excluded:
        diff = market["diff_ratio"]
        if diff > PRICE_DIFF_THRESHOLD:
            is_red = True
            reasons.append(f"Price off by {diff * 100:.1f}%")
        elif diff > PROJECT_PRICE_YELLOW_RATIO:
            is_yellow = True
            reasons.append(f"Price off by {diff * 100:.1f}%")

    p["warn_level"]   = "red" if is_red else ("yellow" if is_yellow else None)
    p["warn_reasons"] = reasons


def _project_priority(p: dict, tracked_names: set[str]) -> int:
    name = p.get("name") or ""
    warn = p.get("warn_level")
    if name in tracked_names:
        return 1
    if warn == "red":
        return 2
    if warn == "yellow":
        return 3
    if name in PROJECT_LOOT_NAMES:
        return 4
    return 5


@app.get("/api/projects/{project_id}/contributors")
async def project_contributors(project_id: str, session: str | None = Cookie(None)):
    character_id = await get_current_character_id(session)
    try:
        access_token = await get_valid_token(character_id)
        contributors = await _cached_project_contributors(project_id, access_token)
    except httpx.HTTPStatusError as e:
        print(f"ESI project contributors HTTP {e.response.status_code}: {e.response.text}")
        if e.response.status_code == 403:
            raise HTTPException(status_code=403, detail="Insufficient corporation roles")
        raise HTTPException(status_code=502, detail="ESI unavailable")
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="ESI unavailable")
    return {"project_id": project_id, "contributors": contributors}


# Jita market history updates ~once/day; cache per type for 6 h to bound ESI load.
_market_history_cache: dict[int, tuple[float, list]] = {}
MARKET_HISTORY_TTL = 6 * 3600


async def _cached_market_history(type_id: int) -> list:
    cached = _market_history_cache.get(type_id)
    if cached and (time.time() - cached[0]) < MARKET_HISTORY_TTL:
        return cached[1]
    rows = await get_market_history(type_id)
    _market_history_cache[type_id] = (time.time(), rows)
    return rows


@app.get("/api/market/{type_id}/history")
async def market_history(type_id: int, session: str | None = Cookie(None)):
    """Last 90 days of Jita market history for a type. Session-protected."""
    await get_current_character_id(session)  # raises 401 without a valid session
    rows = await _cached_market_history(type_id)
    return {"type_id": type_id, "history": rows[-90:]}


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


def _select_contract_icon_type_id(items: list, ship_type_ids: set) -> int | None:
    """Pick the type_id whose icon represents a contract's contents.
    Ship (first found) > the sole item > highest-quantity item. Only
    `is_included` items count; ties on quantity keep list order."""
    included = [i for i in items if i.get("is_included")]
    if not included:
        return None
    for i in included:
        if i.get("type_id") in ship_type_ids:
            return i["type_id"]
    if len(included) == 1:
        return included[0].get("type_id")
    return max(included, key=lambda i: i.get("quantity") or 0).get("type_id")


# Contract contents are immutable, so items are cached per contract_id for the
# process lifetime — a contract is fetched from ESI at most once.
_contract_items_cache: dict[int, list] = {}


async def _enrich_contract_icons(contracts: list, corp_id: int, access_token: str):
    """Attach `icon_type_id` to item_exchange/auction contracts. Best-effort:
    a failed item fetch leaves the contract without an icon and never raises."""
    targets = [c for c in contracts
               if c.get("type") in ("item_exchange", "auction")]
    if not targets:
        return
    ship_type_ids = await get_ship_type_ids()

    async def _items(cid):
        if cid in _contract_items_cache:
            return _contract_items_cache[cid]
        items, _ = await _safe(get_corp_contract_items(corp_id, cid, access_token))
        if items is not None:
            _contract_items_cache[cid] = items
        return items

    fetched = await asyncio.gather(*[_items(c["contract_id"]) for c in targets])
    for c, items in zip(targets, fetched):
        if items is None:
            c["icon_type_id"] = None
            c["is_ship"] = False
            continue
        c["icon_type_id"] = _select_contract_icon_type_id(items, ship_type_ids)
        c["is_ship"] = any(
            i.get("type_id") in ship_type_ids
            for i in items if i.get("is_included"))


def _split_ship_contracts(others: list) -> tuple[list, list]:
    """Partition non-courier/auction contracts into (ships, rest). A contract
    is a ship contract when it has an included ship item — flagged as
    `is_ship` during icon enrichment. Order within each group is preserved."""
    ships = [c for c in others if c.get("is_ship")]
    rest  = [c for c in others if not c.get("is_ship")]
    return ships, rest


# Jita 4-4 prices change often; cache per type for 10 min to bound ESI load.
_jita_price_cache: dict[int, tuple[float, dict]] = {}
JITA_PRICE_TTL = 600


async def _cached_jita_prices(type_id: int) -> dict:
    cached = _jita_price_cache.get(type_id)
    if cached and (time.time() - cached[0]) < JITA_PRICE_TTL:
        return cached[1]
    prices = await get_jita_prices(type_id)
    _jita_price_cache[type_id] = (time.time(), prices)
    return prices


def _build_contract_detail(items: list, names: dict, prices: dict,
                           ship_type_ids: set) -> dict:
    """Assemble the popup payload from a contract's items. Only `is_included`
    items count. Line/summary totals skip a missing (None) unit price."""
    out, buy_sum, sell_sum = [], 0, 0
    for it in items:
        if not it.get("is_included"):
            continue
        tid = it.get("type_id")
        qty = it.get("quantity") or 0
        p = prices.get(tid) or {}
        buy, sell = p.get("buy"), p.get("sell")
        buy_total = buy * qty if buy is not None else None
        sell_total = sell * qty if sell is not None else None
        if buy_total is not None:
            buy_sum += buy_total
        if sell_total is not None:
            sell_sum += sell_total
        out.append({
            "type_id": tid,
            "name": names.get(tid, f"Type #{tid}"),
            "quantity": qty,
            "buy": buy, "sell": sell,
            "buy_total": buy_total, "sell_total": sell_total,
        })
    hero_id = _select_contract_icon_type_id(items, ship_type_ids)
    return {
        "items": out,
        "totals": {"buy": buy_sum, "sell": sell_sum},
        "hero": {"type_id": hero_id, "is_ship": hero_id in ship_type_ids},
    }


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
    (attributes, attr_err), (skillqueue, sq_err), (pvp_income, pvp_income_err) = await asyncio.gather(
        _safe(get_character(character_id, access_token)),
        _safe(get_wallet(character_id, access_token)),
        _safe(get_skills(character_id, access_token)),
        _safe(get_character_location(character_id, access_token)),
        _safe(get_character_online(character_id, access_token)),
        _safe(get_character_ship(character_id, access_token)),
        _safe(get_character_attributes(character_id, access_token)),
        _safe(get_character_skillqueue(character_id, access_token)),
        _safe(_cached_pvp_income(character_id, access_token)),
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
        skills_summary = {
            "total_sp": skills.get("total_sp", 0),
            "unallocated_sp": skills.get("unallocated_sp", 0),
            "training_active": training_active,
            "coverage": await _build_skill_coverage(skills),
        }

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
        "pvp_income": _wrap(pvp_income,      pvp_income_err),
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


@app.get("/api/me/pvp-income")
async def my_pvp_income(page: int = 1, session: str | None = Cookie(None)):
    character_id = await get_current_character_id(session)
    page = max(1, page)
    page_size = 20
    entries, total = await database.list_pvp_entries_page(
        character_id, limit=page_size, offset=(page - 1) * page_size
    )
    total_pages = max(1, (total + page_size - 1) // page_size)
    total_amount = await database.sum_pvp_entries(character_id)
    return {
        "entries": entries,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "total_count": total,
        "total_amount": total_amount,
    }


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


# ── Mail ─────────────────────────────────────────────────────────────────────

# Mail bodies are immutable; cache per (character_id, mail_id) for the process.
_mail_body_cache: dict[tuple[int, int], dict] = {}

# Short cache for the Inbox-unread badge poll so 60s polling never hammers ESI.
_mail_unread_cache: dict[int, tuple[float, int]] = {}  # cid -> (cached_at, inbox_unread)
_MAIL_UNREAD_TTL = 30.0

# Cache for the contacts list used by compose To-field autocomplete.
_mail_contacts_cache: dict[int, tuple[float, list]] = {}
_MAIL_CONTACTS_TTL = 60.0
_MAILABLE_CONTACT_TYPES = ("character", "corporation", "alliance")

INBOX_LABEL_ID = 1


def _mail_scope_http(e: httpx.HTTPStatusError) -> HTTPException:
    print(f"ESI mail HTTP {e.response.status_code}: {e.response.text}")
    if e.response.status_code == 403:
        return HTTPException(
            status_code=403,
            detail="Mail scope not granted — log out and back in to enable Mail.")
    return HTTPException(status_code=502, detail="ESI unavailable")


async def _enrich_mail_names(mails: list[dict]) -> None:
    """Attach `from_name` and recipient `name` to a list of mail dicts in place."""
    ids: list[int] = []
    for m in mails:
        if m.get("from"):
            ids.append(m["from"])
        for rcpt in m.get("recipients", []):
            if rcpt.get("recipient_id"):
                ids.append(rcpt["recipient_id"])
    names = await resolve_names(ids)
    for m in mails:
        m["from_name"] = names.get(m.get("from"))
        for rcpt in m.get("recipients", []):
            rcpt["name"] = names.get(rcpt.get("recipient_id"))


@app.get("/api/me/mail")
async def my_mail(labels: str | None = None, last_mail_id: int | None = None,
                  session: str | None = Cookie(None)):
    character_id = await get_current_character_id(session)
    label_ids = [int(x) for x in labels.split(",")] if labels else None
    try:
        access_token = await get_valid_token(character_id)
        headers = await get_mail_headers(character_id, access_token,
                                         labels=label_ids, last_mail_id=last_mail_id)
    except httpx.HTTPStatusError as e:
        raise _mail_scope_http(e)
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="ESI unavailable")
    await _enrich_mail_names(headers)
    return headers


@app.get("/api/me/mail/labels")
async def my_mail_labels(session: str | None = Cookie(None)):
    character_id = await get_current_character_id(session)
    try:
        access_token = await get_valid_token(character_id)
        labels, lists = await asyncio.gather(
            get_mail_labels(character_id, access_token),
            get_mailing_lists(character_id, access_token),
        )
    except httpx.HTTPStatusError as e:
        raise _mail_scope_http(e)
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="ESI unavailable")
    return {"labels": labels.get("labels", []),
            "total_unread_count": labels.get("total_unread_count", 0),
            "lists": lists}


@app.get("/api/me/mail/unread")
async def my_mail_unread(session: str | None = Cookie(None)):
    character_id = await get_current_character_id(session)
    hit = _mail_unread_cache.get(character_id)
    if hit and (time.time() - hit[0]) < _MAIL_UNREAD_TTL:
        return {"inbox_unread": hit[1]}
    try:
        access_token = await get_valid_token(character_id)
        labels = await get_mail_labels(character_id, access_token)
    except httpx.HTTPStatusError as e:
        raise _mail_scope_http(e)
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="ESI unavailable")
    inbox = next((l for l in labels.get("labels", [])
                  if l.get("label_id") == INBOX_LABEL_ID), {})
    count = inbox.get("unread_count", 0)
    _mail_unread_cache[character_id] = (time.time(), count)
    return {"inbox_unread": count}


@app.get("/api/me/mail/contacts")
async def my_mail_contacts(session: str | None = Cookie(None)):
    character_id = await get_current_character_id(session)
    hit = _mail_contacts_cache.get(character_id)
    if hit and (time.time() - hit[0]) < _MAIL_CONTACTS_TTL:
        return hit[1]
    try:
        access_token = await get_valid_token(character_id)
        contacts = await get_contacts(character_id, access_token)
    except httpx.HTTPStatusError as e:
        raise _mail_scope_http(e)
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="ESI unavailable")
    mailable = [c for c in contacts if c.get("contact_type") in _MAILABLE_CONTACT_TYPES]
    names = await resolve_names([c["contact_id"] for c in mailable])
    out = [{"recipient_id": c["contact_id"],
            "recipient_type": c["contact_type"],
            "name": names.get(c["contact_id"]),
            "standing": c.get("standing", 0.0)} for c in mailable]
    out.sort(key=lambda c: (-(c["standing"] or 0.0), (c["name"] or "").lower()))
    _mail_contacts_cache[character_id] = (time.time(), out)
    return out


@app.post("/api/me/mail/resolve")
async def my_mail_resolve(body: dict, session: str | None = Cookie(None)):
    await get_current_character_id(session)
    names = body.get("names") or []
    return await resolve_recipient_ids(names)


@app.post("/api/me/mail/labels")
async def my_mail_create_label(body: dict, session: str | None = Cookie(None)):
    character_id = await get_current_character_id(session)
    name = (body.get("name") or "").strip()
    color = body.get("color") or "#ffffff"
    if not name:
        raise HTTPException(status_code=400, detail="Label name required")
    try:
        access_token = await get_valid_token(character_id)
        label_id = await create_mail_label(character_id, access_token, name, color)
    except httpx.HTTPStatusError as e:
        raise _mail_scope_http(e)
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="ESI unavailable")
    return {"label_id": label_id}


@app.delete("/api/me/mail/labels/{label_id}")
async def my_mail_delete_label(label_id: int, session: str | None = Cookie(None)):
    character_id = await get_current_character_id(session)
    try:
        access_token = await get_valid_token(character_id)
        await delete_mail_label(character_id, access_token, label_id)
    except httpx.HTTPStatusError as e:
        raise _mail_scope_http(e)
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="ESI unavailable")
    return {"ok": True}


@app.post("/api/me/mail")
async def my_mail_send(body: dict, session: str | None = Cookie(None)):
    character_id = await get_current_character_id(session)
    recipients = body.get("recipients") or []
    subject = (body.get("subject") or "").strip()
    text = body.get("body") or ""
    if not recipients or not subject:
        raise HTTPException(status_code=400, detail="Recipients and subject required")
    try:
        access_token = await get_valid_token(character_id)
        mail_id = await send_mail(character_id, access_token, recipients, subject, text,
                                  approved_cost=int(body.get("approved_cost", 0)))
    except httpx.HTTPStatusError as e:
        print(f"ESI mail send HTTP {e.response.status_code}: {e.response.text}")
        if e.response.status_code == 403:
            raise HTTPException(status_code=403,
                detail="Mail scope not granted — log out and back in to enable Mail.")
        raise HTTPException(status_code=502, detail=f"Send failed: {e.response.text}")
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="ESI unavailable")
    _mail_unread_cache.pop(character_id, None)
    return {"mail_id": mail_id}


@app.put("/api/me/mail/{mail_id}")
async def my_mail_update(mail_id: int, body: dict, session: str | None = Cookie(None)):
    character_id = await get_current_character_id(session)
    read = body.get("read")
    label_ids = body.get("labels")
    try:
        access_token = await get_valid_token(character_id)
        await set_mail(character_id, access_token, mail_id, read=read, labels=label_ids)
    except httpx.HTTPStatusError as e:
        raise _mail_scope_http(e)
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="ESI unavailable")
    _mail_unread_cache.pop(character_id, None)
    _mail_body_cache.pop((character_id, mail_id), None)
    return {"ok": True}


@app.delete("/api/me/mail/{mail_id}")
async def my_mail_delete(mail_id: int, session: str | None = Cookie(None)):
    character_id = await get_current_character_id(session)
    try:
        access_token = await get_valid_token(character_id)
        await delete_mail(character_id, access_token, mail_id)
    except httpx.HTTPStatusError as e:
        raise _mail_scope_http(e)
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="ESI unavailable")
    _mail_unread_cache.pop(character_id, None)
    _mail_body_cache.pop((character_id, mail_id), None)
    return {"ok": True}


@app.get("/api/me/mail/{mail_id}")
async def my_mail_detail(mail_id: int, session: str | None = Cookie(None)):
    character_id = await get_current_character_id(session)
    cached = _mail_body_cache.get((character_id, mail_id))
    if cached:
        return cached
    try:
        access_token = await get_valid_token(character_id)
        mail = await get_mail(character_id, access_token, mail_id)
    except httpx.HTTPStatusError as e:
        raise _mail_scope_http(e)
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="ESI unavailable")
    await _enrich_mail_names([mail])
    _mail_body_cache[(character_id, mail_id)] = mail
    return mail


# ── Fitting ──────────────────────────────────────────────────────────────────

@app.post("/api/me/fitting/eft")
async def my_fitting_eft(body: dict, session: str | None = Cookie(None)):
    await get_current_character_id(session)
    fitting = body.get("fitting") or ""
    fit_name = (body.get("name") or "").strip() or "Fitting"
    ship_id, pairs = parse_fitting_link(fitting)
    if not ship_id:
        raise HTTPException(status_code=400, detail="Not a fitting link")
    try:
        ship_info, *metas = await asyncio.gather(
            get_type_info(ship_id),
            *[get_fitting_type_meta(tid) for tid, _ in pairs],
        )
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="Could not load fitting (ESI unavailable)")
    items = [{**m, "qty": qty} for m, (tid, qty) in zip(metas, pairs)]
    ship_name = ship_info.get("name", f"Type #{ship_id}")
    return {"eft": build_eft(ship_name, fit_name, items), "ship": ship_name}


def _fit_http_error(e: httpx.HTTPError, scope: str) -> HTTPException:
    if isinstance(e, httpx.HTTPStatusError) and e.response.status_code == 403:
        return HTTPException(status_code=403, detail=f"Missing scope: {scope}")
    return HTTPException(status_code=502, detail="ESI unavailable")


@app.get("/api/me/fittings")
async def my_fittings(session: str | None = Cookie(None)):
    character_id = await get_current_character_id(session)
    try:
        token = await get_valid_token(character_id)
        fits = await list_character_fittings(character_id, token)
    except httpx.HTTPError as e:
        raise _fit_http_error(e, "esi-fittings.read_fittings.v1")
    # List stays cheap: resolve only ship names (a small, deduped, cached set).
    # Per-item names + EFT are resolved lazily by /api/me/fittings/detail when a
    # fit's popup is opened, so opening the list never fans out to every type.
    name_map = await resolve_type_names({f["ship_type_id"] for f in fits})
    for f in fits:
        f["ship_name"] = name_map.get(f["ship_type_id"], f"Type #{f['ship_type_id']}")
    return {"fittings": fits}


@app.post("/api/me/fittings/detail")
async def fitting_detail(body: dict, session: str | None = Cookie(None)):
    """Resolve item names + build EFT for a single fit the client already holds
    (from the list). Auth-only — no ESI write — and type names come from the
    cached per-type lookup, so reopening fits that share modules costs nothing."""
    await get_current_character_id(session)
    ship_type_id = body.get("ship_type_id")
    items = body.get("items") or []
    ids = {it["type_id"] for it in items}
    if ship_type_id:
        ids.add(ship_type_id)
    name_map = await resolve_type_names(ids)
    enriched = [{**it, "name": name_map.get(it["type_id"], f"Type #{it['type_id']}")}
                for it in items]
    fitting = {"name": body.get("name") or "Fitting",
               "ship_type_id": ship_type_id, "items": items}
    return {
        "items": enriched,
        "eft": eft_from_esi_fitting(fitting, name_map),
        "ship_name": name_map.get(ship_type_id, f"Type #{ship_type_id}"),
    }


@app.post("/api/me/fittings/import")
async def import_fitting(body: dict, session: str | None = Cookie(None)):
    character_id = await get_current_character_id(session)
    try:
        parsed = parse_eft(body.get("eft") or "")
    except ValueError:
        raise HTTPException(status_code=400, detail="Not a valid EFT fit")
    try:
        payload = await eft_to_esi_fitting(parsed)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="ESI unavailable")
    try:
        token = await get_valid_token(character_id)
        created = await create_character_fitting(character_id, token, payload)
    except httpx.HTTPError as e:
        raise _fit_http_error(e, "esi-fittings.write_fittings.v1")
    return {"fitting_id": created.get("fitting_id"), "name": payload["name"]}


@app.delete("/api/me/fittings/{fitting_id}")
async def remove_fitting(fitting_id: int, session: str | None = Cookie(None)):
    character_id = await get_current_character_id(session)
    try:
        token = await get_valid_token(character_id)
        await delete_character_fitting(character_id, token, fitting_id)
    except httpx.HTTPError as e:
        raise _fit_http_error(e, "esi-fittings.write_fittings.v1")
    return {"ok": True}


@app.post("/api/me/fittings/{fitting_id}/duplicate")
async def duplicate_fitting(fitting_id: int, session: str | None = Cookie(None)):
    character_id = await get_current_character_id(session)
    try:
        token = await get_valid_token(character_id)
        fits = await list_character_fittings(character_id, token)
    except httpx.HTTPError as e:
        raise _fit_http_error(e, "esi-fittings.read_fittings.v1")
    src = next((f for f in fits if f["fitting_id"] == fitting_id), None)
    if src is None:
        raise HTTPException(status_code=404, detail="Fitting not found")
    name = next_copy_name(src.get("name") or "Fitting", {f.get("name") for f in fits})
    try:
        created = await create_character_fitting(
            character_id, token, fitting_to_create_payload(src, name))
    except httpx.HTTPError as e:
        raise _fit_http_error(e, "esi-fittings.write_fittings.v1")
    return {"fitting_id": created.get("fitting_id"), "name": name}


@app.post("/api/me/fittings/{fitting_id}/rename")
async def rename_fitting(fitting_id: int, body: dict, session: str | None = Cookie(None)):
    character_id = await get_current_character_id(session)
    new_name = (body.get("name") or "").strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="Name required")
    try:
        token = await get_valid_token(character_id)
        fits = await list_character_fittings(character_id, token)
    except httpx.HTTPError as e:
        raise _fit_http_error(e, "esi-fittings.read_fittings.v1")
    src = next((f for f in fits if f["fitting_id"] == fitting_id), None)
    if src is None:
        raise HTTPException(status_code=404, detail="Fitting not found")
    # Create the renamed copy FIRST; only delete the original once it exists.
    try:
        created = await create_character_fitting(
            character_id, token, fitting_to_create_payload(src, new_name))
    except httpx.HTTPError as e:
        raise _fit_http_error(e, "esi-fittings.write_fittings.v1")
    try:
        await delete_character_fitting(character_id, token, fitting_id)
    except httpx.HTTPError:
        raise HTTPException(status_code=502,
                            detail="Renamed copy created, but the original could not be removed")
    return {"fitting_id": created.get("fitting_id"), "name": new_name[:50]}


# ── Static files (must be last) ───────────────────────────────────────────────

app.mount("/", StaticFiles(directory="static", html=True), name="static")
