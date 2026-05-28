import pytest

from db import (
    init_db, set_project_track, list_tracked_projects,
    list_project_settings, set_project_special, set_project_price_excluded,
)


@pytest.fixture(autouse=True)
async def _db():
    await init_db()


async def test_init_db_is_idempotent():
    await init_db()  # second run must not raise (track column already present)


async def test_set_and_list_tracked_projects():
    await set_project_track("Armor Remote Repair", True)
    await set_project_track("Remote Boost Shield", True)
    await set_project_track("Ordinary Project", False)
    assert await list_tracked_projects() == {"Armor Remote Repair", "Remote Boost Shield"}


async def test_track_is_independent_of_special_and_excluded():
    await set_project_track("Armor Remote Repair", True)
    await set_project_special("Armor Remote Repair", True)
    await set_project_price_excluded("Armor Remote Repair", False)
    settings = {s["project_name"]: s for s in await list_project_settings()}
    row = settings["Armor Remote Repair"]
    assert row["track"] == 1
    assert row["special"] == 1
    assert row["price_excluded"] == 0


async def test_fresh_db_seeds_legacy_track_names():
    # init_db ran in the autouse fixture — fresh DB should have the two legacy
    # names seeded with track=1.
    assert await list_tracked_projects() == {"Remote Boost Shield", "Armor Remote Repair"}


async def test_seed_does_not_overwrite_after_admin_disable():
    # Admin turns one off, then init_db runs again (next app boot). Flag must
    # stay off — the seed only runs when the `track` column is added.
    await set_project_track("Armor Remote Repair", False)
    await init_db()
    assert await list_tracked_projects() == {"Remote Boost Shield"}
