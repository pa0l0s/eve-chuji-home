import time

import pytest
import respx
from httpx import Response

from db import init_db, get_ship_types_cache, set_ship_types_cache
from esi import get_ship_type_ids, get_corp_contract_items


@pytest.fixture(autouse=True)
async def setup_db():
    await init_db()


@pytest.fixture
def _select_contract_icon_type_id(set_env):
    import importlib
    import main
    importlib.reload(main)
    return main._select_contract_icon_type_id


def _item(type_id, qty=1, included=True):
    return {"record_id": type_id, "type_id": type_id,
            "quantity": qty, "is_included": included, "is_singleton": False}


# ── _select_contract_icon_type_id ──────────────────────────────────────────

def test_icon_single_included_item_returns_its_type(_select_contract_icon_type_id):
    assert _select_contract_icon_type_id([_item(587)], set()) == 587


def test_icon_ship_among_multiple_items_wins(_select_contract_icon_type_id):
    items = [_item(34, qty=1000), _item(670, qty=1), _item(587, qty=1)]
    assert _select_contract_icon_type_id(items, {587}) == 587


def test_icon_first_ship_when_several_ships(_select_contract_icon_type_id):
    items = [_item(34), _item(670), _item(587)]
    assert _select_contract_icon_type_id(items, {670, 587}) == 670


def test_icon_multi_no_ship_picks_highest_quantity(_select_contract_icon_type_id):
    items = [_item(34, qty=10), _item(35, qty=999), _item(36, qty=5)]
    assert _select_contract_icon_type_id(items, set()) == 35


def test_icon_quantity_tie_picks_first_listed(_select_contract_icon_type_id):
    items = [_item(34, qty=100), _item(35, qty=100)]
    assert _select_contract_icon_type_id(items, set()) == 34


def test_icon_ignores_excluded_items(_select_contract_icon_type_id):
    items = [_item(34, qty=1, included=False), _item(35, qty=1, included=True)]
    assert _select_contract_icon_type_id(items, set()) == 35


def test_icon_excluded_ship_does_not_count(_select_contract_icon_type_id):
    items = [_item(587, qty=1, included=False), _item(35, qty=1, included=True)]
    assert _select_contract_icon_type_id(items, {587}) == 35


def test_icon_no_items_returns_none(_select_contract_icon_type_id):
    assert _select_contract_icon_type_id([], {587}) is None


def test_icon_only_excluded_items_returns_none(_select_contract_icon_type_id):
    assert _select_contract_icon_type_id([_item(34, included=False)], set()) is None


# ── ship-types cache (db) ──────────────────────────────────────────────────

async def test_ship_types_cache_empty_returns_none():
    assert await get_ship_types_cache() is None


async def test_ship_types_cache_set_then_get():
    await set_ship_types_cache({1, 2, 3})
    cached = await get_ship_types_cache()
    assert cached["type_ids"] == {1, 2, 3}
    assert cached["fetched_at"] == pytest.approx(time.time(), abs=5)


async def test_ship_types_cache_overwrites():
    await set_ship_types_cache({1, 2})
    await set_ship_types_cache({9})
    cached = await get_ship_types_cache()
    assert cached["type_ids"] == {9}


# ── get_ship_type_ids (esi) ────────────────────────────────────────────────

@respx.mock
async def test_get_ship_type_ids_builds_from_esi_and_caches():
    respx.get("https://esi.evetech.net/latest/universe/categories/6/").mock(
        return_value=Response(200, json={"category_id": 6, "groups": [25, 26]}))
    respx.get("https://esi.evetech.net/latest/universe/groups/25/").mock(
        return_value=Response(200, json={"group_id": 25, "types": [587, 588]}))
    respx.get("https://esi.evetech.net/latest/universe/groups/26/").mock(
        return_value=Response(200, json={"group_id": 26, "types": [670]}))

    ids = await get_ship_type_ids()
    assert ids == {587, 588, 670}
    # Persisted so a later call within the TTL needs no ESI.
    assert (await get_ship_types_cache())["type_ids"] == {587, 588, 670}


@respx.mock
async def test_get_ship_type_ids_uses_fresh_cache_without_esi():
    await set_ship_types_cache({111, 222})
    # No respx routes registered: any ESI call would raise.
    assert await get_ship_type_ids() == {111, 222}


@respx.mock
async def test_get_ship_type_ids_refetches_when_cache_stale():
    await set_ship_types_cache({111}, fetched_at=time.time() - 3 * 86400)
    respx.get("https://esi.evetech.net/latest/universe/categories/6/").mock(
        return_value=Response(200, json={"groups": [25]}))
    respx.get("https://esi.evetech.net/latest/universe/groups/25/").mock(
        return_value=Response(200, json={"types": [999]}))
    assert await get_ship_type_ids() == {999}


@respx.mock
async def test_get_ship_type_ids_returns_stale_on_esi_failure():
    await set_ship_types_cache({111}, fetched_at=time.time() - 3 * 86400)
    respx.get("https://esi.evetech.net/latest/universe/categories/6/").mock(
        return_value=Response(500))
    assert await get_ship_type_ids() == {111}


# ── get_corp_contract_items (esi) ──────────────────────────────────────────

@respx.mock
async def test_get_corp_contract_items_fetches():
    respx.get(
        "https://esi.evetech.net/latest/corporations/98340844/contracts/42/items/"
    ).mock(return_value=Response(200, json=[
        {"record_id": 1, "type_id": 587, "quantity": 1,
         "is_included": True, "is_singleton": True},
    ]))
    items = await get_corp_contract_items(98340844, 42, "tok")
    assert items[0]["type_id"] == 587


# ── _enrich_contract_icons (endpoint glue) ─────────────────────────────────

@pytest.fixture
def _enrich_contract_icons(set_env):
    import importlib
    import main
    importlib.reload(main)
    main._contract_items_cache.clear()
    return main._enrich_contract_icons


def _ic_route(cid, body):
    return respx.get(
        f"https://esi.evetech.net/latest/corporations/98340844/contracts/{cid}/items/"
    ).mock(return_value=Response(200, json=body))


@respx.mock
async def test_enrich_attaches_icon_for_item_exchange_and_auction(_enrich_contract_icons):
    await set_ship_types_cache({587})
    _ic_route(1, [{"type_id": 999, "quantity": 1, "is_included": True}])
    _ic_route(2, [{"type_id": 34, "quantity": 5, "is_included": True},
                  {"type_id": 587, "quantity": 1, "is_included": True}])
    contracts = [
        {"contract_id": 1, "type": "item_exchange"},
        {"contract_id": 2, "type": "auction"},
        {"contract_id": 3, "type": "courier"},
    ]
    await _enrich_contract_icons(contracts, 98340844, "tok")
    assert contracts[0]["icon_type_id"] == 999
    assert contracts[1]["icon_type_id"] == 587   # ship wins
    assert contracts[2].get("icon_type_id") is None  # courier untouched


@respx.mock
async def test_enrich_caches_items_per_contract(_enrich_contract_icons):
    await set_ship_types_cache(set())
    route = _ic_route(7, [{"type_id": 42, "quantity": 1, "is_included": True}])
    c = [{"contract_id": 7, "type": "item_exchange"}]
    await _enrich_contract_icons(c, 98340844, "tok")
    await _enrich_contract_icons(c, 98340844, "tok")
    assert route.call_count == 1
    assert c[0]["icon_type_id"] == 42


@respx.mock
async def test_enrich_is_best_effort_on_item_fetch_failure(_enrich_contract_icons):
    await set_ship_types_cache(set())
    respx.get(
        "https://esi.evetech.net/latest/corporations/98340844/contracts/5/items/"
    ).mock(return_value=Response(403))
    c = [{"contract_id": 5, "type": "item_exchange"}]
    await _enrich_contract_icons(c, 98340844, "tok")  # must not raise
    assert c[0].get("icon_type_id") is None


# ── is_ship flag + ship/other split ────────────────────────────────────────

@pytest.fixture
def main_mod(set_env):
    import importlib
    import main
    importlib.reload(main)
    main._contract_items_cache.clear()
    return main


@respx.mock
async def test_enrich_sets_is_ship_flag(_enrich_contract_icons):
    await set_ship_types_cache({587})
    _ic_route(1, [{"type_id": 587, "quantity": 1, "is_included": True}])
    _ic_route(2, [{"type_id": 34, "quantity": 5, "is_included": True}])
    _ic_route(3, [{"type_id": 587, "quantity": 1, "is_included": False}])
    contracts = [
        {"contract_id": 1, "type": "item_exchange"},
        {"contract_id": 2, "type": "item_exchange"},
        {"contract_id": 3, "type": "item_exchange"},
        {"contract_id": 4, "type": "courier"},
    ]
    await _enrich_contract_icons(contracts, 98340844, "tok")
    assert contracts[0]["is_ship"] is True
    assert contracts[1]["is_ship"] is False
    assert contracts[2]["is_ship"] is False     # excluded ship doesn't count
    assert contracts[3].get("is_ship") is None   # courier untouched


@respx.mock
async def test_enrich_is_ship_false_on_item_fetch_failure(_enrich_contract_icons):
    await set_ship_types_cache({587})
    respx.get(
        "https://esi.evetech.net/latest/corporations/98340844/contracts/9/items/"
    ).mock(return_value=Response(403))
    c = [{"contract_id": 9, "type": "item_exchange"}]
    await _enrich_contract_icons(c, 98340844, "tok")
    assert c[0]["is_ship"] is False


def test_split_ship_contracts_partitions_by_flag(main_mod):
    others = [
        {"contract_id": 1, "is_ship": True},
        {"contract_id": 2, "is_ship": False},
        {"contract_id": 3, "is_ship": True},
        {"contract_id": 4},  # no flag (e.g. loan) → rest
    ]
    ships, rest = main_mod._split_ship_contracts(others)
    assert [c["contract_id"] for c in ships] == [1, 3]
    assert [c["contract_id"] for c in rest] == [2, 4]
