import pytest

from db import (
    init_db,
    upsert_pvp_entries_batch,
    list_pvp_entries_page,
    sum_pvp_entries,
)


CID = 2122515161


@pytest.fixture(autouse=True)
async def _db():
    await init_db()


async def test_init_db_idempotent_for_pvp_entries():
    await init_db()  # second run must not raise


async def test_empty_listing():
    rows, total = await list_pvp_entries_page(CID, limit=10, offset=0)
    assert rows == []
    assert total == 0
    assert await sum_pvp_entries(CID) == 0


async def test_upsert_batch_empty_is_noop():
    await upsert_pvp_entries_batch(CID, [])
    rows, total = await list_pvp_entries_page(CID, limit=10, offset=0)
    assert rows == [] and total == 0


async def test_upsert_round_trip_newest_first():
    await upsert_pvp_entries_batch(CID, [
        {"id": 1, "date": "2026-01-01T00:00:00Z", "amount": 100.0,
         "reason": "Astrahus", "description": "Cerberus Agnon ..."},
        {"id": 2, "date": "2026-03-01T00:00:00Z", "amount": 200.0,
         "reason": "PVP1", "description": "Cerberus Agnon ..."},
        {"id": 3, "date": "2026-02-01T00:00:00Z", "amount": 50.0,
         "reason": "PVP2", "description": "Cerberus Agnon ..."},
    ])
    rows, total = await list_pvp_entries_page(CID, limit=10, offset=0)
    assert total == 3
    assert [r["amount"] for r in rows] == [200.0, 50.0, 100.0]  # by date desc
    # Each row exposes only the columns the modal needs.
    assert set(rows[0].keys()) == {"date", "amount", "reason"}


async def test_upsert_conflict_does_not_overwrite():
    await upsert_pvp_entries_batch(CID, [
        {"id": 1, "date": "2026-01-01T00:00:00Z", "amount": 100.0,
         "reason": "Astrahus", "description": "first version"},
    ])
    await upsert_pvp_entries_batch(CID, [
        {"id": 1, "date": "2026-01-01T00:00:00Z", "amount": 999.0,
         "reason": "DIFFERENT", "description": "second version"},
    ])
    rows, total = await list_pvp_entries_page(CID, limit=10, offset=0)
    assert total == 1
    assert rows[0]["amount"] == 100.0
    assert rows[0]["reason"] == "Astrahus"


async def test_pagination_limit_offset():
    await upsert_pvp_entries_batch(CID, [
        {"id": i, "date": f"2026-01-{i:02d}T00:00:00Z", "amount": float(i),
         "reason": f"r{i}", "description": ""}
        for i in range(1, 6)
    ])
    page1, total = await list_pvp_entries_page(CID, limit=2, offset=0)
    page2, _    = await list_pvp_entries_page(CID, limit=2, offset=2)
    page3, _    = await list_pvp_entries_page(CID, limit=2, offset=4)
    assert total == 5
    assert [r["amount"] for r in page1] == [5.0, 4.0]
    assert [r["amount"] for r in page2] == [3.0, 2.0]
    assert [r["amount"] for r in page3] == [1.0]


async def test_sum_pvp_entries():
    await upsert_pvp_entries_batch(CID, [
        {"id": 1, "date": "2026-01-01T00:00:00Z", "amount": 100.0,
         "reason": "", "description": ""},
        {"id": 2, "date": "2026-02-01T00:00:00Z", "amount": 250.5,
         "reason": "", "description": ""},
    ])
    assert await sum_pvp_entries(CID) == 350.5


async def test_per_character_isolation():
    await upsert_pvp_entries_batch(CID, [
        {"id": 1, "date": "2026-01-01T00:00:00Z", "amount": 100.0,
         "reason": "", "description": ""},
    ])
    await upsert_pvp_entries_batch(999, [
        {"id": 1, "date": "2026-01-01T00:00:00Z", "amount": 5.0,
         "reason": "", "description": ""},
    ])
    rows_a, total_a = await list_pvp_entries_page(CID, limit=10, offset=0)
    rows_b, total_b = await list_pvp_entries_page(999, limit=10, offset=0)
    assert total_a == 1 and rows_a[0]["amount"] == 100.0
    assert total_b == 1 and rows_b[0]["amount"] == 5.0


import respx
from httpx import Response


JOURNAL_URL = f"https://esi.evetech.net/latest/characters/{CID}/wallet/journal/"


SAMPLE_JOURNAL = [
    {
        "id": 11, "amount": 100.0,
        "date": "2026-05-01T00:00:00Z",
        "ref_type": "corporation_account_withdrawal",
        "description": "Cerberus Agnon transferred cash to player",
        "reason": "Astrahus",
    },
    {
        "id": 12, "amount": 200.0,
        "date": "2026-05-02T00:00:00Z",
        "ref_type": "corporation_account_withdrawal",
        "description": "Cerberus Agnon transferred cash again",
        "reason": "PVP",
    },
    {
        "id": 13, "amount": 999.0,
        "date": "2026-05-03T00:00:00Z",
        "ref_type": "player_donation",
        "description": "From Cerberus Agnon",
        "reason": "Gift",
    },
    {
        "id": 14, "amount": 999.0,
        "date": "2026-05-04T00:00:00Z",
        "ref_type": "corporation_account_withdrawal",
        "description": "Someone Else transferred cash",
        "reason": "x",
    },
]


@pytest.fixture(autouse=True)
def _main_module(set_env):
    import main
    main._pvp_income_cache.clear()
    yield main
    main._pvp_income_cache.clear()


@respx.mock
async def test_cached_pvp_income_persists_only_matching_entries(_main_module):
    respx.get(JOURNAL_URL).mock(return_value=Response(
        200, json=SAMPLE_JOURNAL, headers={"X-Pages": "1"},
    ))
    sum_returned = await _main_module._cached_pvp_income(CID, "tok")
    assert sum_returned == 300.0  # only the two matching entries
    rows, total = await list_pvp_entries_page(CID, limit=10, offset=0)
    assert total == 2
    assert {int(r["amount"]) for r in rows} == {100, 200}


@respx.mock
async def test_cached_pvp_income_cache_hit_skips_db_write(_main_module):
    import time
    _main_module._pvp_income_cache[CID] = (time.time(), 42.0)
    result = await _main_module._cached_pvp_income(CID, "tok")
    assert result == 42.0
    rows, total = await list_pvp_entries_page(CID, limit=10, offset=0)
    assert total == 0  # cache hit did not write anything


@respx.mock
async def test_cached_pvp_income_persists_idempotently(_main_module):
    respx.get(JOURNAL_URL).mock(return_value=Response(
        200, json=SAMPLE_JOURNAL, headers={"X-Pages": "1"},
    ))
    await _main_module._cached_pvp_income(CID, "tok")
    cached_at, amount = _main_module._pvp_income_cache[CID]
    _main_module._pvp_income_cache[CID] = (cached_at - 301, amount)
    await _main_module._cached_pvp_income(CID, "tok")
    _, total = await list_pvp_entries_page(CID, limit=10, offset=0)
    assert total == 2


import time

from db import upsert_token
from auth import make_session_cookie


async def _user_cookie(character_id: int = CID) -> dict:
    await upsert_token(
        character_id=character_id, character_name="Pilot",
        access_token="acc", refresh_token="ref",
        expires_at=time.time() + 1200, corporation_id=98340844,
    )
    return {"session": make_session_cookie(character_id)}


async def test_pvp_income_endpoint_requires_auth(client):
    r = await client.get("/api/me/pvp-income")
    assert r.status_code == 401


async def test_pvp_income_endpoint_empty(client):
    cookies = await _user_cookie()
    r = await client.get("/api/me/pvp-income", cookies=cookies)
    assert r.status_code == 200
    body = r.json()
    assert body["entries"] == []
    assert body["total_count"] == 0
    assert body["total_amount"] == 0
    assert body["page"] == 1
    assert body["page_size"] == 20
    assert body["total_pages"] == 1


async def test_pvp_income_endpoint_paged(client):
    await upsert_pvp_entries_batch(CID, [
        {"id": i, "date": f"2026-01-{i:02d}T00:00:00Z", "amount": float(i),
         "reason": f"r{i}", "description": ""}
        for i in range(1, 26)
    ])
    cookies = await _user_cookie()

    r1 = await client.get("/api/me/pvp-income?page=1", cookies=cookies)
    body1 = r1.json()
    assert body1["page"] == 1 and body1["page_size"] == 20
    assert body1["total_count"] == 25 and body1["total_pages"] == 2
    assert body1["total_amount"] == sum(range(1, 26))
    assert len(body1["entries"]) == 20
    assert body1["entries"][0]["amount"] == 25.0
    assert body1["entries"][-1]["amount"] == 6.0
    assert set(body1["entries"][0].keys()) == {"date", "amount", "reason"}

    r2 = await client.get("/api/me/pvp-income?page=2", cookies=cookies)
    body2 = r2.json()
    assert body2["page"] == 2
    assert len(body2["entries"]) == 5
    assert body2["total_amount"] == body1["total_amount"]
    assert body2["total_count"] == 25


async def test_pvp_income_endpoint_out_of_range_page(client):
    await upsert_pvp_entries_batch(CID, [
        {"id": 1, "date": "2026-01-01T00:00:00Z", "amount": 5.0,
         "reason": "", "description": ""},
    ])
    cookies = await _user_cookie()
    r = await client.get("/api/me/pvp-income?page=999", cookies=cookies)
    body = r.json()
    assert body["entries"] == []
    assert body["total_count"] == 1
    assert body["total_amount"] == 5.0
    assert body["total_pages"] == 1


async def test_pvp_income_endpoint_clamps_negative_page(client):
    cookies = await _user_cookie()
    r = await client.get("/api/me/pvp-income?page=-3", cookies=cookies)
    assert r.json()["page"] == 1
