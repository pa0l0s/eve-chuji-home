import respx
from httpx import Response
from esi import parse_fitting_link, get_fitting_type_meta, _fitting_meta_cache


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
