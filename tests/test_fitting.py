import time
import respx
from httpx import Response
from esi import parse_fitting_link, get_fitting_type_meta, _fitting_meta_cache, build_eft
from db import upsert_token
from auth import make_session_cookie


def test_parse_fitting_basic():
    href = "fitting:621:31360;1:1877;5:27361;1000::"
    ship_id, items = parse_fitting_link(href)
    assert ship_id == 621
    assert items == [(31360, 1), (1877, 5), (27361, 1000)]


def test_parse_fitting_defaults_qty_when_missing():
    ship_id, items = parse_fitting_link("fitting:621:31360::")
    assert ship_id == 621
    assert items == [(31360, 1)]


def test_parse_fitting_rejects_non_fitting():
    assert parse_fitting_link("showinfo:1377//94409715") == (0, [])
    assert parse_fitting_link("fitting:abc:1;1::") == (0, [])


T = "https://esi.evetech.net/latest/universe/types"
G = "https://esi.evetech.net/latest/universe/groups"


@respx.mock
async def test_meta_low_module():
    _fitting_meta_cache.clear()
    respx.get(f"{T}/100/").mock(return_value=Response(200, json={
        "name": "Damage Control II", "group_id": 60,
        "dogma_effects": [{"effect_id": 11, "is_default": True}]}))
    m = await get_fitting_type_meta(100)
    assert m == {"name": "Damage Control II", "slot": "low", "kind": "module"}


@respx.mock
async def test_meta_high_module():
    _fitting_meta_cache.clear()
    respx.get(f"{T}/101/").mock(return_value=Response(200, json={
        "name": "Heavy Missile Launcher II", "group_id": 510,
        "dogma_effects": [{"effect_id": 12}]}))
    m = await get_fitting_type_meta(101)
    assert m["slot"] == "high" and m["kind"] == "module"


@respx.mock
async def test_meta_rig_module():
    _fitting_meta_cache.clear()
    respx.get(f"{T}/102/").mock(return_value=Response(200, json={
        "name": "Medium Core Defense Field Extender I", "group_id": 781,
        "dogma_effects": [{"effect_id": 2663}]}))
    m = await get_fitting_type_meta(102)
    assert m["slot"] == "rig" and m["kind"] == "module"


@respx.mock
async def test_meta_drone():
    _fitting_meta_cache.clear()
    respx.get(f"{T}/103/").mock(return_value=Response(200, json={
        "name": "Hobgoblin II", "group_id": 100, "dogma_effects": []}))
    respx.get(f"{G}/100/").mock(return_value=Response(200, json={
        "group_id": 100, "name": "Light Scout Drone", "category_id": 18}))
    m = await get_fitting_type_meta(103)
    assert m == {"name": "Hobgoblin II", "slot": None, "kind": "drone"}


@respx.mock
async def test_meta_charge_and_cache():
    _fitting_meta_cache.clear()
    route = respx.get(f"{T}/104/").mock(return_value=Response(200, json={
        "name": "Scourge Heavy Missile", "group_id": 384, "dogma_effects": [{"effect_id": 9}]}))
    respx.get(f"{G}/384/").mock(return_value=Response(200, json={
        "group_id": 384, "name": "Heavy Missile", "category_id": 8}))
    m1 = await get_fitting_type_meta(104)
    m2 = await get_fitting_type_meta(104)   # served from cache
    assert m1 == {"name": "Scourge Heavy Missile", "slot": None, "kind": "charge"}
    assert m2 == m1
    assert route.call_count == 1


@respx.mock
async def test_meta_degrades_to_charge_on_esi_error():
    _fitting_meta_cache.clear()
    respx.get(f"{T}/999/").mock(return_value=Response(500))
    m = await get_fitting_type_meta(999)
    assert m == {"name": "Type #999", "slot": None, "kind": "charge"}


def test_build_eft_groups_and_formats():
    items = [
        {"name": "Damage Control II", "slot": "low", "kind": "module", "qty": 1},
        {"name": "Large Shield Extender II", "slot": "mid", "kind": "module", "qty": 1},
        {"name": "Heavy Missile Launcher II", "slot": "high", "kind": "module", "qty": 3},
        {"name": "Medium Core Defense Field Extender I", "slot": "rig", "kind": "module", "qty": 1},
        {"name": "Hobgoblin II", "slot": None, "kind": "drone", "qty": 2},
        {"name": "Scourge Heavy Missile", "slot": None, "kind": "charge", "qty": 1000},
    ]
    eft = build_eft("Caracal", "test", items)
    assert eft == (
        "[Caracal, test]\n"
        "Damage Control II\n"
        "\n"
        "Large Shield Extender II\n"
        "\n"
        "Heavy Missile Launcher II\n"
        "Heavy Missile Launcher II\n"
        "Heavy Missile Launcher II\n"
        "\n"
        "Medium Core Defense Field Extender I\n"
        "\n"
        "Hobgoblin II x2\n"
        "\n"
        "Scourge Heavy Missile x1000"
    )


TYPES = "https://esi.evetech.net/latest/universe/types"
GROUPS = "https://esi.evetech.net/latest/universe/groups"


async def _fit_auth(character_id=777):
    await upsert_token(character_id=character_id, character_name="Pilot",
                       access_token="a", refresh_token="r",
                       expires_at=time.time() + 1200, corporation_id=98340844)
    return {"session": make_session_cookie(character_id)}


async def test_fitting_eft_requires_auth(client):
    r = await client.post("/api/me/fitting/eft", json={"fitting": "fitting:621:100;1::", "name": "x"})
    assert r.status_code == 401


async def test_fitting_eft_rejects_non_fitting(client):
    cookies = await _fit_auth()
    r = await client.post("/api/me/fitting/eft",
                          json={"fitting": "showinfo:1377//1", "name": "x"}, cookies=cookies)
    assert r.status_code == 400


@respx.mock
async def test_fitting_eft_happy_path(client):
    _fitting_meta_cache.clear()
    cookies = await _fit_auth()
    respx.get(f"{TYPES}/621/").mock(return_value=Response(200, json={
        "name": "Caracal", "group_id": 26}))
    respx.get(f"{TYPES}/100/").mock(return_value=Response(200, json={
        "name": "Damage Control II", "group_id": 60,
        "dogma_effects": [{"effect_id": 11}]}))
    respx.get(f"{TYPES}/200/").mock(return_value=Response(200, json={
        "name": "Hobgoblin II", "group_id": 100, "dogma_effects": []}))
    respx.get(f"{GROUPS}/100/").mock(return_value=Response(200, json={
        "group_id": 100, "name": "Light Scout Drone", "category_id": 18}))
    r = await client.post("/api/me/fitting/eft", cookies=cookies,
                          json={"fitting": "fitting:621:100;1:200;2::", "name": "ess"})
    assert r.status_code == 200
    data = r.json()
    assert data["ship"] == "Caracal"
    assert data["eft"] == (
        "[Caracal, ess]\n"
        "Damage Control II\n"
        "\n"
        "Hobgoblin II x2"
    )
