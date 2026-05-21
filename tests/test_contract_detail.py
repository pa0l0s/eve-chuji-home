import time

import pytest
import respx
from httpx import Response

from db import init_db
from esi import get_jita_sell_min, get_jita_prices

ORDERS_URL = "https://esi.evetech.net/latest/markets/10000002/orders/"
JITA_44 = 60003760


@pytest.fixture(autouse=True)
async def setup_db():
    await init_db()


@pytest.fixture
def main_mod(set_env):
    import importlib
    import main
    importlib.reload(main)
    main._jita_price_cache.clear()
    return main


def _order(price, location_id=JITA_44):
    return {"price": price, "location_id": location_id}


# ── get_jita_sell_min ──────────────────────────────────────────────────────

@respx.mock
async def test_sell_min_returns_lowest_at_44():
    respx.get(ORDERS_URL, params__contains={"order_type": "sell"}).mock(
        return_value=Response(200, json=[_order(500), _order(300), _order(900)]))
    assert await get_jita_sell_min(34) == 300


@respx.mock
async def test_sell_min_ignores_other_stations():
    respx.get(ORDERS_URL, params__contains={"order_type": "sell"}).mock(
        return_value=Response(200, json=[_order(100, location_id=1), _order(450)]))
    assert await get_jita_sell_min(34) == 450


@respx.mock
async def test_sell_min_none_when_no_orders():
    respx.get(ORDERS_URL, params__contains={"order_type": "sell"}).mock(
        return_value=Response(200, json=[]))
    assert await get_jita_sell_min(34) is None


@respx.mock
async def test_sell_min_none_on_http_error():
    respx.get(ORDERS_URL, params__contains={"order_type": "sell"}).mock(
        return_value=Response(500))
    assert await get_jita_sell_min(34) is None


# ── get_jita_prices ────────────────────────────────────────────────────────

@respx.mock
async def test_jita_prices_combines_buy_and_sell():
    respx.get(ORDERS_URL, params__contains={"order_type": "buy"}).mock(
        return_value=Response(200, json=[_order(100), _order(220)]))
    respx.get(ORDERS_URL, params__contains={"order_type": "sell"}).mock(
        return_value=Response(200, json=[_order(800), _order(650)]))
    assert await get_jita_prices(34) == {"buy": 220, "sell": 650}


@respx.mock
async def test_jita_prices_partial_when_sell_missing():
    respx.get(ORDERS_URL, params__contains={"order_type": "buy"}).mock(
        return_value=Response(200, json=[_order(100)]))
    respx.get(ORDERS_URL, params__contains={"order_type": "sell"}).mock(
        return_value=Response(200, json=[]))
    assert await get_jita_prices(34) == {"buy": 100, "sell": None}


# ── _build_contract_detail ─────────────────────────────────────────────────

def _item(type_id, qty=1, included=True):
    return {"type_id": type_id, "quantity": qty, "is_included": included}


def test_detail_filters_excluded_items(main_mod):
    items = [_item(34, included=False), _item(35, qty=2)]
    names = {35: "Tritanium"}
    prices = {35: {"buy": 5, "sell": 7}}
    d = main_mod._build_contract_detail(items, names, prices, set())
    assert [i["type_id"] for i in d["items"]] == [35]
    assert d["items"][0]["name"] == "Tritanium"


def test_detail_line_and_summary_totals(main_mod):
    items = [_item(35, qty=2), _item(36, qty=3)]
    names = {35: "A", 36: "B"}
    prices = {35: {"buy": 5, "sell": 7}, 36: {"buy": 10, "sell": 20}}
    d = main_mod._build_contract_detail(items, names, prices, set())
    assert d["items"][0]["buy_total"] == 10 and d["items"][0]["sell_total"] == 14
    assert d["totals"] == {"buy": 10 + 30, "sell": 14 + 60}


def test_detail_none_price_propagates_and_skipped_in_totals(main_mod):
    items = [_item(35, qty=4), _item(36, qty=1)]
    names = {35: "A", 36: "B"}
    prices = {35: {"buy": None, "sell": 7}, 36: {"buy": 9, "sell": None}}
    d = main_mod._build_contract_detail(items, names, prices, set())
    assert d["items"][0]["buy_total"] is None
    assert d["items"][0]["sell_total"] == 28
    assert d["totals"] == {"buy": 9, "sell": 28}


def test_detail_name_fallback_when_unresolved(main_mod):
    d = main_mod._build_contract_detail([_item(999)], {}, {}, set())
    assert d["items"][0]["name"] == "Type #999"
    assert d["items"][0]["buy"] is None


def test_detail_hero_is_ship(main_mod):
    items = [_item(34, qty=99), _item(587, qty=1)]
    d = main_mod._build_contract_detail(items, {}, {}, {587})
    assert d["hero"] == {"type_id": 587, "is_ship": True}


def test_detail_hero_non_ship(main_mod):
    d = main_mod._build_contract_detail([_item(35)], {}, {}, set())
    assert d["hero"] == {"type_id": 35, "is_ship": False}


def test_detail_no_items(main_mod):
    d = main_mod._build_contract_detail([], {}, {}, set())
    assert d["items"] == []
    assert d["totals"] == {"buy": 0, "sell": 0}
    assert d["hero"]["type_id"] is None


# ── _cached_jita_prices (TTL) ──────────────────────────────────────────────

@respx.mock
async def test_cached_jita_prices_fetches_then_caches(main_mod):
    route_b = respx.get(ORDERS_URL, params__contains={"order_type": "buy"}).mock(
        return_value=Response(200, json=[_order(50)]))
    respx.get(ORDERS_URL, params__contains={"order_type": "sell"}).mock(
        return_value=Response(200, json=[_order(80)]))
    p1 = await main_mod._cached_jita_prices(34)
    p2 = await main_mod._cached_jita_prices(34)
    assert p1 == {"buy": 50, "sell": 80} == p2
    assert route_b.call_count == 1  # second call served from cache


@respx.mock
async def test_cached_jita_prices_refetches_when_stale(main_mod):
    main_mod._jita_price_cache[34] = (time.time() - 9999, {"buy": 1, "sell": 2})
    respx.get(ORDERS_URL, params__contains={"order_type": "buy"}).mock(
        return_value=Response(200, json=[_order(7)]))
    respx.get(ORDERS_URL, params__contains={"order_type": "sell"}).mock(
        return_value=Response(200, json=[_order(9)]))
    assert await main_mod._cached_jita_prices(34) == {"buy": 7, "sell": 9}
