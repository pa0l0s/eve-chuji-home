import pytest

from db import (
    init_db, set_project_special, list_special_projects,
    list_project_settings, set_project_price_excluded,
)


@pytest.fixture(autouse=True)
async def _db():
    await init_db()


async def test_init_db_is_idempotent():
    await init_db()  # second run must not raise (special column already present)


async def test_set_and_list_special_projects():
    await set_project_special("Armor Remote Repair", True)
    await set_project_special("Remote Boost Shield", True)
    await set_project_special("Ordinary Project", False)
    assert await list_special_projects() == {"Armor Remote Repair", "Remote Boost Shield"}


async def test_special_and_price_excluded_are_independent():
    await set_project_special("Armor Remote Repair", True)
    await set_project_price_excluded("Armor Remote Repair", False)
    settings = {s["project_name"]: s for s in await list_project_settings()}
    assert settings["Armor Remote Repair"]["special"] == 1
    assert settings["Armor Remote Repair"]["price_excluded"] == 0


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


async def test_admin_set_special_requires_admin(client):
    cookies = await _admin_cookie(555)  # not in ADMIN_CHARACTER_IDS
    r = await client.post("/api/admin/projects/settings",
                          json={"name": "Armor Remote Repair", "special": True},
                          cookies=cookies)
    assert r.status_code == 403


async def test_admin_set_special_flag(client, monkeypatch):
    monkeypatch.setenv("ADMIN_CHARACTER_IDS", "777")
    cookies = await _admin_cookie(777)
    r = await client.post("/api/admin/projects/settings",
                          json={"name": "Armor Remote Repair", "special": True},
                          cookies=cookies)
    assert r.status_code == 200
    assert "Armor Remote Repair" in await list_special_projects()


async def test_admin_set_special_leaves_price_excluded(client, monkeypatch):
    monkeypatch.setenv("ADMIN_CHARACTER_IDS", "777")
    cookies = await _admin_cookie(777)
    await client.post("/api/admin/projects/settings",
                      json={"name": "P1", "excluded": True}, cookies=cookies)
    await client.post("/api/admin/projects/settings",
                      json={"name": "P1", "special": True}, cookies=cookies)
    settings = {s["project_name"]: s for s in await list_project_settings()}
    assert settings["P1"]["price_excluded"] == 1
    assert settings["P1"]["special"] == 1


import respx
from httpx import Response

CORP = "98340844"  # conftest sets CORP_ID=98340844
PROJECTS_URL = f"https://esi.evetech.net/corporations/{CORP}/projects"
IDS_URL = "https://esi.evetech.net/latest/universe/ids/"


def _contrib_url(project_id: str) -> str:
    return f"https://esi.evetech.net/corporations/{CORP}/projects/{project_id}/contributors"


@respx.mock
async def test_project_contributors_endpoint_caches(client):
    route = respx.get(_contrib_url("p1")).mock(return_value=Response(200, json={
        "contributors": [{"id": 1, "name": "Alice", "contributed": 10}],
    }))
    cookies = await _admin_cookie(777)
    await client.get("/api/projects/p1/contributors", cookies=cookies)
    await client.get("/api/projects/p1/contributors", cookies=cookies)
    assert route.call_count == 1


@respx.mock
async def test_projects_marks_special_and_top_contributors(client):
    await set_project_special("Armor Remote Repair", True)
    respx.get(PROJECTS_URL).mock(return_value=Response(200, json={"projects": [
        {"id": "p1", "name": "Armor Remote Repair", "state": "Active",
         "progress": {"current": 5, "desired": 10}},
    ]}))
    respx.post(IDS_URL).mock(return_value=Response(200, json={}))
    respx.get(_contrib_url("p1")).mock(return_value=Response(200, json={"contributors": [
        {"id": 1, "name": "Alice", "contributed": 100},
        {"id": 2, "name": "Bob", "contributed": 300},
        {"id": 3, "name": "Carol", "contributed": 50},
        {"id": 4, "name": "Dave", "contributed": 200},
    ]}))
    cookies = await _admin_cookie(777)
    r = await client.get("/api/projects", cookies=cookies)
    assert r.status_code == 200
    proj = next(p for p in r.json()["active"] if p["name"] == "Armor Remote Repair")
    assert proj["special"] is True
    assert [c["name"] for c in proj["top_contributors"]] == ["Bob", "Dave", "Alice"]
