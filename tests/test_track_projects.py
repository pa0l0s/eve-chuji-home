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


import time

from db import upsert_token
from auth import make_session_cookie


async def _admin_cookie(character_id: int = 777) -> dict:
    await upsert_token(
        character_id=character_id, character_name="Admin Pilot",
        access_token="acc", refresh_token="ref",
        expires_at=time.time() + 1200, corporation_id=98340844,
    )
    return {"session": make_session_cookie(character_id)}


async def test_admin_set_track_requires_admin(client):
    cookies = await _admin_cookie(555)  # not in ADMIN_CHARACTER_IDS
    r = await client.post("/api/admin/projects/settings",
                          json={"name": "My Project", "track": True},
                          cookies=cookies)
    assert r.status_code == 403


async def test_admin_set_track_flag(client, monkeypatch):
    monkeypatch.setenv("ADMIN_CHARACTER_IDS", "777")
    cookies = await _admin_cookie(777)
    r = await client.post("/api/admin/projects/settings",
                          json={"name": "My Project", "track": True},
                          cookies=cookies)
    assert r.status_code == 200
    assert "My Project" in await list_tracked_projects()


async def test_admin_track_independent_from_other_flags(client, monkeypatch):
    monkeypatch.setenv("ADMIN_CHARACTER_IDS", "777")
    cookies = await _admin_cookie(777)
    await client.post("/api/admin/projects/settings",
                      json={"name": "P1", "excluded": True}, cookies=cookies)
    await client.post("/api/admin/projects/settings",
                      json={"name": "P1", "special": True}, cookies=cookies)
    await client.post("/api/admin/projects/settings",
                      json={"name": "P1", "track": True}, cookies=cookies)
    settings = {s["project_name"]: s for s in await list_project_settings()}
    assert settings["P1"]["price_excluded"] == 1
    assert settings["P1"]["special"] == 1
    assert settings["P1"]["track"] == 1


import respx
from httpx import Response

CORP = "98340844"  # conftest sets CORP_ID=98340844
PROJECTS_URL = f"https://esi.evetech.net/corporations/{CORP}/projects"
IDS_URL = "https://esi.evetech.net/latest/universe/ids/"


@respx.mock
async def test_projects_sort_priority_uses_db_track_flag(client, monkeypatch):
    # Admin marks an unusual name (NOT one of the legacy hard-coded names) as
    # tracked. /api/projects must give it sort_priority=1.
    monkeypatch.setenv("ADMIN_CHARACTER_IDS", "777")
    cookies = await _admin_cookie(777)
    await client.post("/api/admin/projects/settings",
                      json={"name": "Custom Tracked Project", "track": True},
                      cookies=cookies)

    respx.get(PROJECTS_URL).mock(return_value=Response(200, json={"projects": [
        {"id": "p1", "name": "Custom Tracked Project", "state": "Active",
         "progress": {"current": 1, "desired": 10}},
        {"id": "p2", "name": "Unrelated Project", "state": "Active",
         "progress": {"current": 1, "desired": 10}},
    ]}))
    respx.post(IDS_URL).mock(return_value=Response(200, json={}))

    r = await client.get("/api/projects", cookies=cookies)
    assert r.status_code == 200
    actives = {p["name"]: p for p in r.json()["active"]}
    assert actives["Custom Tracked Project"]["sort_priority"] == 1
    assert actives["Unrelated Project"]["sort_priority"] == 5
