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
