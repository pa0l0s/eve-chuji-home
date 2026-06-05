import time
import respx
from httpx import Response

from esi import (
    flag_to_slot_kind, eft_from_esi_fitting, parse_eft, eft_to_esi_fitting,
    list_character_fittings, create_character_fitting, delete_character_fitting,
    _fitting_meta_cache, fitting_to_create_payload, next_copy_name,
)
from db import init_db, upsert_token
from auth import make_session_cookie

import pytest


@pytest.fixture(autouse=True)
async def setup_db():
    await init_db()


async def _auth_cookie(character_id=123) -> dict:
    await upsert_token(
        character_id=character_id, character_name="Test Pilot",
        access_token="valid_acc", refresh_token="valid_ref",
        expires_at=time.time() + 1200, corporation_id=98340844,
    )
    return {"session": make_session_cookie(character_id)}


def test_flag_to_slot_kind_modules():
    assert flag_to_slot_kind("HiSlot0") == ("high", "module")
    assert flag_to_slot_kind("MedSlot3") == ("mid", "module")
    assert flag_to_slot_kind("LoSlot7") == ("low", "module")
    assert flag_to_slot_kind("RigSlot1") == ("rig", "module")
    assert flag_to_slot_kind("SubSystemSlot2") == ("subsystem", "module")


def test_flag_to_slot_kind_drone_and_cargo():
    assert flag_to_slot_kind("DroneBay") == (None, "drone")
    assert flag_to_slot_kind("Cargo") == (None, "charge")
    assert flag_to_slot_kind("FighterBay") == (None, "charge")


def test_eft_from_esi_fitting_groups_and_names():
    fitting = {
        "name": "My Rifter", "ship_type_id": 587,
        "items": [
            {"type_id": 100, "quantity": 1, "flag": "LoSlot0"},
            {"type_id": 200, "quantity": 1, "flag": "HiSlot0"},
            {"type_id": 300, "quantity": 5, "flag": "DroneBay"},
            {"type_id": 400, "quantity": 1000, "flag": "Cargo"},
        ],
    }
    name_map = {587: "Rifter", 100: "Damage Control II", 200: "125mm Gatling AutoCannon II",
                300: "Hobgoblin II", 400: "EMP S"}
    eft = eft_from_esi_fitting(fitting, name_map)
    assert eft.splitlines()[0] == "[Rifter, My Rifter]"
    assert "Damage Control II" in eft
    assert "125mm Gatling AutoCannon II" in eft
    assert "Hobgoblin II x5" in eft
    assert "EMP S x1000" in eft


def test_eft_from_esi_fitting_unknown_name_degrades():
    fitting = {"name": "F", "ship_type_id": 9, "items": [{"type_id": 8, "quantity": 1, "flag": "LoSlot0"}]}
    eft = eft_from_esi_fitting(fitting, {})
    assert eft.splitlines()[0] == "[Type #9, F]"
    assert "Type #8" in eft


def test_parse_eft_basic_groups_and_charge():
    text = (
        "[Rifter, My Rifter]\n"
        "Damage Control II\n"
        "\n"
        "200mm AutoCannon II, EMP S\n"
        "\n"
        "Small Projectile Burst Aerator I\n"
        "\n"
        "Hobgoblin II x5\n"
        "EMP S x1000\n"
    )
    p = parse_eft(text)
    assert p["ship_name"] == "Rifter"
    assert p["fit_name"] == "My Rifter"
    names = [l["name"] for l in p["lines"]]
    assert names == ["Damage Control II", "200mm AutoCannon II",
                     "Small Projectile Burst Aerator I", "Hobgoblin II", "EMP S"]
    ac = next(l for l in p["lines"] if l["name"] == "200mm AutoCannon II")
    assert ac["charge"] == "EMP S"
    drone = next(l for l in p["lines"] if l["name"] == "Hobgoblin II")
    assert drone["qty"] == 5


def test_parse_eft_skips_empty_slot_markers_and_blank_lines():
    text = "[Rifter, X]\n[Empty High slot]\n\nDamage Control II\n"
    p = parse_eft(text)
    assert [l["name"] for l in p["lines"]] == ["Damage Control II"]


def test_parse_eft_defaults_fit_name():
    p = parse_eft("[Rifter, ]\nDamage Control II")
    assert p["fit_name"] == "Imported Fit"


def test_parse_eft_rejects_missing_header():
    with pytest.raises(ValueError):
        parse_eft("Damage Control II\n200mm AutoCannon II")
    with pytest.raises(ValueError):
        parse_eft("")


def test_parse_eft_charge_with_quantity_strips_qty():
    p = parse_eft("[Rifter, X]\n200mm AutoCannon II, EMP S x10")
    line = p["lines"][0]
    assert line["name"] == "200mm AutoCannon II"
    assert line["charge"] == "EMP S"      # qty stripped from charge
    assert line["qty"] == 1


def test_parse_eft_zero_quantity_clamped_to_one():
    p = parse_eft("[Rifter, X]\nHobgoblin II x0")
    assert p["lines"][0]["qty"] == 1


def test_parse_eft_handles_crlf():
    p = parse_eft("[Rifter, X]\r\nDamage Control II\r\n")
    assert p["ship_name"] == "Rifter"
    assert [l["name"] for l in p["lines"]] == ["Damage Control II"]


IDS = "https://esi.evetech.net/latest/universe/ids/"
TYPES = "https://esi.evetech.net/latest/universe/types"


@respx.mock
async def test_eft_to_esi_fitting_assigns_flags_and_charge():
    _fitting_meta_cache.clear()
    respx.post(IDS).mock(return_value=Response(200, json={"inventory_types": [
        {"id": 587, "name": "Rifter"},
        {"id": 100, "name": "Damage Control II"},
        {"id": 200, "name": "200mm AutoCannon II"},
        {"id": 210, "name": "EMP S"},
        {"id": 300, "name": "Hobgoblin II"},
    ]}))
    # Damage Control II -> low module
    respx.get(f"{TYPES}/100/").mock(return_value=Response(200, json={
        "name": "Damage Control II", "group_id": 60,
        "dogma_effects": [{"effect_id": 11}]}))
    # 200mm AutoCannon II -> high module
    respx.get(f"{TYPES}/200/").mock(return_value=Response(200, json={
        "name": "200mm AutoCannon II", "group_id": 55,
        "dogma_effects": [{"effect_id": 12}]}))
    # EMP S -> charge (no slot dogma, not a drone group)
    respx.get(f"{TYPES}/210/").mock(return_value=Response(200, json={
        "name": "EMP S", "group_id": 83, "dogma_effects": []}))
    respx.get("https://esi.evetech.net/latest/universe/groups/83/").mock(
        return_value=Response(200, json={"category_id": 8}))
    # Hobgoblin II -> drone
    respx.get(f"{TYPES}/300/").mock(return_value=Response(200, json={
        "name": "Hobgoblin II", "group_id": 100, "dogma_effects": []}))
    respx.get("https://esi.evetech.net/latest/universe/groups/100/").mock(
        return_value=Response(200, json={"category_id": 18}))

    parsed = {"ship_name": "Rifter", "fit_name": "My Rifter", "lines": [
        {"name": "Damage Control II", "charge": None, "qty": 1},
        {"name": "200mm AutoCannon II", "charge": "EMP S", "qty": 1},
        {"name": "Hobgoblin II", "charge": None, "qty": 5},
    ]}
    payload = await eft_to_esi_fitting(parsed)
    assert payload["ship_type_id"] == 587
    assert payload["name"] == "My Rifter"
    flags = {(i["type_id"], i["flag"], i["quantity"]) for i in payload["items"]}
    assert (100, "LoSlot0", 1) in flags
    assert (200, "HiSlot0", 1) in flags
    assert (210, "HiSlot0", 1) in flags     # charge inherits the module's flag
    assert (300, "DroneBay", 5) in flags


@respx.mock
async def test_eft_to_esi_fitting_unresolved_name_raises():
    respx.post(IDS).mock(return_value=Response(200, json={"inventory_types": [
        {"id": 587, "name": "Rifter"}]}))
    parsed = {"ship_name": "Rifter", "fit_name": "X", "lines": [
        {"name": "Nonexistent Module", "charge": None, "qty": 1}]}
    with pytest.raises(ValueError) as exc:
        await eft_to_esi_fitting(parsed)
    assert "Nonexistent Module" in str(exc.value)


@respx.mock
async def test_eft_to_esi_fitting_qty_gt_1_module_with_charge():
    """A qty>1 module emits one item per unit with sequential flags, and an
    inline charge inherits the LAST assigned slot flag."""
    _fitting_meta_cache.clear()
    respx.post(IDS).mock(return_value=Response(200, json={"inventory_types": [
        {"id": 587, "name": "Rifter"},
        {"id": 200, "name": "200mm AutoCannon II"},
        {"id": 210, "name": "EMP S"},
    ]}))
    respx.get(f"{TYPES}/200/").mock(return_value=Response(200, json={
        "name": "200mm AutoCannon II", "group_id": 55,
        "dogma_effects": [{"effect_id": 12}]}))      # high slot
    respx.get(f"{TYPES}/210/").mock(return_value=Response(200, json={
        "name": "EMP S", "group_id": 83, "dogma_effects": []}))
    respx.get("https://esi.evetech.net/latest/universe/groups/83/").mock(
        return_value=Response(200, json={"category_id": 8}))   # charge, not drone

    parsed = {"ship_name": "Rifter", "fit_name": "X", "lines": [
        {"name": "200mm AutoCannon II", "charge": "EMP S", "qty": 2},
    ]}
    payload = await eft_to_esi_fitting(parsed)
    items = payload["items"]
    # two module units in consecutive high slots
    assert {"type_id": 200, "quantity": 1, "flag": "HiSlot0"} in items
    assert {"type_id": 200, "quantity": 1, "flag": "HiSlot1"} in items
    # one charge, inheriting the LAST module flag
    charges = [i for i in items if i["type_id"] == 210]
    assert charges == [{"type_id": 210, "quantity": 1, "flag": "HiSlot1"}]


# ── Route tests ───────────────────────────────────────────────────────────────

FITS = "https://esi.evetech.net/characters/123/fittings"


async def test_my_fittings_requires_auth(client):
    r = await client.get("/api/me/fittings")
    assert r.status_code == 401


@respx.mock
async def test_my_fittings_happy_path(client):
    respx.get(f"{FITS}/").mock(return_value=Response(200, json=[{
        "fitting_id": 1, "name": "My Rifter", "description": "",
        "ship_type_id": 587,
        "items": [{"type_id": 100, "quantity": 1, "flag": "LoSlot0"}],
    }]))
    # The list resolves ONLY the ship name (cheap); item names + EFT are loaded
    # lazily by /api/me/fittings/detail. So only the ship type GET is needed.
    respx.get(f"{TYPES}/587/").mock(return_value=Response(200, json={
        "name": "Rifter", "group_id": 25}))
    cookies = await _auth_cookie()
    r = await client.get("/api/me/fittings", cookies=cookies)
    assert r.status_code == 200
    fits = r.json()["fittings"]
    assert fits[0]["ship_name"] == "Rifter"
    assert fits[0]["items"][0]["type_id"] == 100   # raw item kept for the popup
    assert "eft" not in fits[0]                      # not prebuilt on the list


async def test_fitting_detail_requires_auth(client):
    r = await client.post("/api/me/fittings/detail",
                          json={"ship_type_id": 587, "name": "X", "items": []})
    assert r.status_code == 401


@respx.mock
async def test_fitting_detail_resolves_names_and_eft(client):
    # Names resolve per-type via get_type_info; a single unresolvable id degrades
    # only itself (the old bulk /universe/names path 404'd the whole batch).
    respx.get(f"{TYPES}/587/").mock(return_value=Response(200, json={"name": "Rifter", "group_id": 25}))
    respx.get(f"{TYPES}/100/").mock(return_value=Response(200, json={"name": "Damage Control II", "group_id": 60}))
    respx.get(f"{TYPES}/999/").mock(return_value=Response(404, json={}))
    cookies = await _auth_cookie()
    r = await client.post("/api/me/fittings/detail", cookies=cookies, json={
        "ship_type_id": 587, "name": "My Rifter",
        "items": [{"type_id": 100, "quantity": 1, "flag": "LoSlot0"},
                  {"type_id": 999, "quantity": 1, "flag": "HiSlot0"}]})
    assert r.status_code == 200
    d = r.json()
    assert d["ship_name"] == "Rifter"
    names = {i["type_id"]: i["name"] for i in d["items"]}
    assert names[100] == "Damage Control II"
    assert names[999] == "Type #999"            # bad id degrades alone
    assert d["eft"].splitlines()[0] == "[Rifter, My Rifter]"


@respx.mock
async def test_my_fittings_403_maps_to_scope(client):
    respx.get(f"{FITS}/").mock(return_value=Response(403, json={}))
    cookies = await _auth_cookie()
    r = await client.get("/api/me/fittings", cookies=cookies)
    assert r.status_code == 403
    assert "esi-fittings.read_fittings.v1" in r.json()["detail"]


@respx.mock
async def test_my_fittings_502(client):
    respx.get(f"{FITS}/").mock(return_value=Response(500, json={}))
    cookies = await _auth_cookie()
    r = await client.get("/api/me/fittings", cookies=cookies)
    assert r.status_code == 502


@respx.mock
async def test_import_fitting_happy_path(client):
    _fitting_meta_cache.clear()
    respx.post("https://esi.evetech.net/latest/universe/ids/").mock(
        return_value=Response(200, json={"inventory_types": [
            {"id": 587, "name": "Rifter"},
            {"id": 100, "name": "Damage Control II"}]}))
    respx.get("https://esi.evetech.net/latest/universe/types/100/").mock(
        return_value=Response(200, json={"name": "Damage Control II",
            "group_id": 60, "dogma_effects": [{"effect_id": 11}]}))
    respx.post(f"{FITS}/").mock(return_value=Response(200, json={"fitting_id": 42}))
    cookies = await _auth_cookie()
    r = await client.post("/api/me/fittings/import",
                          json={"eft": "[Rifter, My Rifter]\nDamage Control II"},
                          cookies=cookies)
    assert r.status_code == 200
    assert r.json()["fitting_id"] == 42


async def test_import_fitting_rejects_junk(client):
    cookies = await _auth_cookie()
    r = await client.post("/api/me/fittings/import",
                          json={"eft": "just some text\nno header"}, cookies=cookies)
    assert r.status_code == 400


@respx.mock
async def test_delete_fitting_happy_path(client):
    respx.delete(f"{FITS}/7/").mock(return_value=Response(204))
    cookies = await _auth_cookie()
    r = await client.delete("/api/me/fittings/7", cookies=cookies)
    assert r.status_code == 200
    assert r.json() == {"ok": True}


async def test_delete_fitting_requires_auth(client):
    r = await client.delete("/api/me/fittings/7")
    assert r.status_code == 401


async def test_import_fitting_requires_auth(client):
    r = await client.post("/api/me/fittings/import", json={"eft": "[Rifter, X]"})
    assert r.status_code == 401


def test_next_copy_name_first_copy():
    assert next_copy_name("Rifter", set()) == "Rifter copy"
    assert next_copy_name("Rifter", {"Rifter"}) == "Rifter copy"


def test_next_copy_name_increments():
    assert next_copy_name("Rifter", {"Rifter copy"}) == "Rifter copy2"
    assert next_copy_name("Rifter", {"Rifter copy", "Rifter copy2"}) == "Rifter copy3"


def test_next_copy_name_caps_50_chars():
    base = "x" * 60
    assert len(next_copy_name(base, set())) == 50


def test_fitting_to_create_payload_copies_items_only():
    fit = {
        "fitting_id": 9, "name": "Orig", "ship_name": "Rifter", "eft": "...",
        "description": "<p>d</p>", "ship_type_id": 587,
        "items": [{"type_id": 100, "quantity": 2, "flag": "LoSlot0", "name": "DCU II"}],
    }
    p = fitting_to_create_payload(fit, "New Name")
    assert p == {
        "name": "New Name", "description": "<p>d</p>", "ship_type_id": 587,
        "items": [{"type_id": 100, "quantity": 2, "flag": "LoSlot0"}],
    }


@respx.mock
async def test_duplicate_fitting_happy_path(client):
    respx.get(f"{FITS}/").mock(return_value=Response(200, json=[{
        "fitting_id": 5, "name": "Rifter", "description": "", "ship_type_id": 587,
        "items": [{"type_id": 100, "quantity": 1, "flag": "LoSlot0"}],
    }]))
    create = respx.post(f"{FITS}/").mock(return_value=Response(200, json={"fitting_id": 88}))
    cookies = await _auth_cookie()
    r = await client.post("/api/me/fittings/5/duplicate", cookies=cookies)
    assert r.status_code == 200
    assert r.json() == {"fitting_id": 88, "name": "Rifter copy"}
    import json as _json
    sent = _json.loads(create.calls.last.request.content)
    assert sent["name"] == "Rifter copy"
    assert sent["ship_type_id"] == 587
    assert sent["items"] == [{"type_id": 100, "quantity": 1, "flag": "LoSlot0"}]


@respx.mock
async def test_duplicate_fitting_unknown_id_404(client):
    respx.get(f"{FITS}/").mock(return_value=Response(200, json=[]))
    cookies = await _auth_cookie()
    r = await client.post("/api/me/fittings/999/duplicate", cookies=cookies)
    assert r.status_code == 404


@respx.mock
async def test_rename_fitting_happy_path(client):
    respx.get(f"{FITS}/").mock(return_value=Response(200, json=[{
        "fitting_id": 5, "name": "Rifter", "description": "", "ship_type_id": 587,
        "items": [{"type_id": 100, "quantity": 1, "flag": "LoSlot0"}],
    }]))
    create = respx.post(f"{FITS}/").mock(return_value=Response(200, json={"fitting_id": 90}))
    delete = respx.delete(f"{FITS}/5/").mock(return_value=Response(204))
    cookies = await _auth_cookie()
    r = await client.post("/api/me/fittings/5/rename",
                          json={"name": "Better Rifter"}, cookies=cookies)
    assert r.status_code == 200
    assert r.json() == {"fitting_id": 90, "name": "Better Rifter"}
    assert create.called and delete.called


async def test_rename_fitting_empty_name_400(client):
    cookies = await _auth_cookie()
    r = await client.post("/api/me/fittings/5/rename", json={"name": "  "}, cookies=cookies)
    assert r.status_code == 400


async def test_duplicate_requires_auth(client):
    r = await client.post("/api/me/fittings/5/duplicate")
    assert r.status_code == 401


def test_next_copy_name_increments_when_truncated_copy_exists():
    base = "x" * 48
    first = next_copy_name(base, set())          # capped to 50
    second = next_copy_name(base, {first})       # must differ and stay <=50
    assert second != first
    assert len(second) <= 50


@respx.mock
async def test_rename_fitting_unknown_id_404(client):
    respx.get(f"{FITS}/").mock(return_value=Response(200, json=[]))
    cookies = await _auth_cookie()
    r = await client.post("/api/me/fittings/999/rename", json={"name": "X"}, cookies=cookies)
    assert r.status_code == 404


@respx.mock
async def test_rename_fitting_delete_failure_502(client):
    respx.get(f"{FITS}/").mock(return_value=Response(200, json=[{
        "fitting_id": 5, "name": "Rifter", "description": "", "ship_type_id": 587,
        "items": [{"type_id": 100, "quantity": 1, "flag": "LoSlot0"}]}]))
    respx.post(f"{FITS}/").mock(return_value=Response(200, json={"fitting_id": 90}))
    respx.delete(f"{FITS}/5/").mock(return_value=Response(500, json={}))
    cookies = await _auth_cookie()
    r = await client.post("/api/me/fittings/5/rename", json={"name": "New"}, cookies=cookies)
    assert r.status_code == 502
    assert "could not be removed" in r.json()["detail"]


async def test_rename_requires_auth(client):
    r = await client.post("/api/me/fittings/5/rename", json={"name": "X"})
    assert r.status_code == 401
