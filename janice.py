import time
import httpx

from db import get_janice_cache, upsert_janice_cache

# Janice appraisal API — accepts EVE paste text, returns item prices
# Response schema assumption: {"items": [{"typeID": int, "name": str, "prices": {"buy": {"fivePercent": float}}}]}
# Verify against current Janice API if the integration fails.
JANICE_APPRAISAL_URL = "https://janice.e-351.com/api/rest/v2/appraisal"
CACHE_TTL = 3600  # 1 hour


async def get_prices_for_items(type_id_to_name: dict[int, str]) -> dict[int, float]:
    """Accepts {type_id: item_name} mapping, returns {type_id: corp_buy_price (90% Jita)}."""
    result: dict[int, float] = {}
    to_fetch: dict[int, str] = {}

    for type_id, name in type_id_to_name.items():
        cached = await get_janice_cache(type_id)
        if cached and time.time() - cached["cached_at"] < CACHE_TTL:
            result[type_id] = cached["buy_price"]
        else:
            to_fetch[type_id] = name

    if to_fetch:
        fetched = await _fetch_prices(to_fetch)
        now = time.time()
        for type_id, price in fetched.items():
            await upsert_janice_cache(type_id, price, now)
            result[type_id] = price

    return result


async def _fetch_prices(type_id_to_name: dict[int, str]) -> dict[int, float]:
    # Build EVE paste-format text: "Item Name x 1" per line
    paste_text = "\n".join(f"{name} x 1" for name in type_id_to_name.values())
    name_to_type_id = {v: k for k, v in type_id_to_name.items()}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                JANICE_APPRAISAL_URL,
                params={"market": "jita", "persist": "false", "compactize": "true"},
                content=paste_text,
                headers={"Content-Type": "text/plain"},
            )
            r.raise_for_status()
            data = r.json()

        result: dict[int, float] = {}
        for item in data.get("items", []):
            name = item.get("name")
            type_id = name_to_type_id.get(name) or item.get("typeID")
            buy_five_pct = item.get("prices", {}).get("buy", {}).get("fivePercent", 0)
            if type_id and buy_five_pct:
                result[type_id] = buy_five_pct * 0.90
        return result
    except Exception:
        return {}
