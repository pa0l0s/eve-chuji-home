import time
import pytest
import respx
from httpx import Response
from db import init_db, upsert_janice_cache
from janice import get_prices_for_items


JANICE_URL = "https://janice.e-351.com/api/rest/v2/appraisal"


@pytest.fixture(autouse=True)
async def setup_db():
    await init_db()


@respx.mock
async def test_fetches_prices_from_api():
    respx.post(JANICE_URL).mock(
        return_value=Response(200, json={
            "items": [
                {"typeID": 34, "name": "Tritanium", "prices": {"buy": {"fivePercent": 5.0}}},
                {"typeID": 35, "name": "Pyerite",   "prices": {"buy": {"fivePercent": 10.0}}},
            ]
        })
    )
    prices = await get_prices_for_items({34: "Tritanium", 35: "Pyerite"})
    assert prices[34] == pytest.approx(5.0 * 0.90)
    assert prices[35] == pytest.approx(10.0 * 0.90)


@respx.mock
async def test_uses_cache_when_fresh():
    await upsert_janice_cache(item_id=34, buy_price=4.5, cached_at=time.time())
    # API should NOT be called — 34 is cached, nothing to fetch
    respx.post(JANICE_URL).mock(return_value=Response(500))
    prices = await get_prices_for_items({34: "Tritanium"})
    assert prices[34] == pytest.approx(4.5)


@respx.mock
async def test_refetches_when_cache_expired():
    await upsert_janice_cache(item_id=34, buy_price=1.0, cached_at=time.time() - 7200)
    respx.post(JANICE_URL).mock(
        return_value=Response(200, json={
            "items": [
                {"typeID": 34, "name": "Tritanium", "prices": {"buy": {"fivePercent": 5.0}}},
            ]
        })
    )
    prices = await get_prices_for_items({34: "Tritanium"})
    assert prices[34] == pytest.approx(5.0 * 0.90)


@respx.mock
async def test_returns_empty_dict_on_api_error():
    respx.post(JANICE_URL).mock(return_value=Response(503))
    prices = await get_prices_for_items({34: "Tritanium", 35: "Pyerite"})
    assert prices == {}
