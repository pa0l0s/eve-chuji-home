import respx
from httpx import Response

from esi import get_market_history

HISTORY_URL = "https://esi.evetech.net/latest/markets/10000002/history/"


@respx.mock
async def test_market_history_returns_records_sorted_by_date():
    respx.get(HISTORY_URL).mock(return_value=Response(200, json=[
        {"date": "2026-03-02", "average": 5.2, "highest": 5.5, "lowest": 5.0},
        {"date": "2026-03-01", "average": 5.0, "highest": 5.3, "lowest": 4.8},
    ]))
    out = await get_market_history(34)
    assert [d["date"] for d in out] == ["2026-03-01", "2026-03-02"]


@respx.mock
async def test_market_history_returns_empty_on_http_error():
    respx.get(HISTORY_URL).mock(return_value=Response(404))
    assert await get_market_history(999999) == []
