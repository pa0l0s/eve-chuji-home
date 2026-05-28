import pytest
import respx
from httpx import Response

from esi import get_character_wallet_journal


CID = 2122515161
JOURNAL_URL = f"https://esi.evetech.net/latest/characters/{CID}/wallet/journal/"


@respx.mock
async def test_journal_single_page():
    respx.get(JOURNAL_URL).mock(return_value=Response(
        200,
        json=[{"id": 1, "amount": 100, "ref_type": "x", "description": ""}],
        headers={"X-Pages": "1"},
    ))
    entries = await get_character_wallet_journal(CID, "tok")
    assert entries == [{"id": 1, "amount": 100, "ref_type": "x", "description": ""}]


@respx.mock
async def test_journal_walks_multiple_pages():
    route = respx.get(JOURNAL_URL).mock(side_effect=[
        Response(200, json=[{"id": 1}, {"id": 2}], headers={"X-Pages": "2"}),
        Response(200, json=[{"id": 3}],            headers={"X-Pages": "2"}),
    ])
    entries = await get_character_wallet_journal(CID, "tok")
    assert [e["id"] for e in entries] == [1, 2, 3]
    assert route.call_count == 2


@respx.mock
async def test_journal_stops_on_empty_page():
    # Defensive: ESI sometimes omits/zeros X-Pages; an empty list ends the walk.
    respx.get(JOURNAL_URL).mock(return_value=Response(200, json=[]))
    entries = await get_character_wallet_journal(CID, "tok")
    assert entries == []


SAMPLE_JOURNAL = [
    {
        "id": 1, "amount": 371_000_000,
        "ref_type": "corporation_account_withdrawal",
        "description": "Cerberus Agnon transferred cash from corp account to player",
    },
    {
        "id": 2, "amount": 50_000_000,
        "ref_type": "corporation_account_withdrawal",
        "description": "Cerberus Agnon transferred cash again",
    },
    {
        "id": 3, "amount": 99_999,
        "ref_type": "corporation_account_withdrawal",
        "description": "Someone Else transferred cash",
    },
    {
        "id": 4, "amount": 1,
        "ref_type": "player_donation",
        "description": "Gift from Cerberus Agnon",
    },
    {
        "id": 5, "amount": 7,
        "ref_type": "corporation_account_withdrawal",
        "description": None,
    },
]


@pytest.fixture(autouse=True)
def _main_module(set_env):
    import main
    main._pvp_income_cache.clear()
    yield main
    main._pvp_income_cache.clear()


def test_sum_pvp_income_includes_only_matching_entries(_main_module):
    assert _main_module._sum_pvp_income(SAMPLE_JOURNAL) == 371_000_000 + 50_000_000


def test_sum_pvp_income_empty(_main_module):
    assert _main_module._sum_pvp_income([]) == 0


def test_sum_pvp_income_none_description_safe(_main_module):
    entry_with_none = [{
        "id": 1, "amount": 5,
        "ref_type": "corporation_account_withdrawal",
        "description": None,
    }]
    assert _main_module._sum_pvp_income(entry_with_none) == 0


@respx.mock
async def test_cached_pvp_income_caches_within_ttl(_main_module):
    route = respx.get(JOURNAL_URL).mock(return_value=Response(
        200, json=SAMPLE_JOURNAL, headers={"X-Pages": "1"},
    ))
    first  = await _main_module._cached_pvp_income(CID, "tok")
    second = await _main_module._cached_pvp_income(CID, "tok")
    assert first == 371_000_000 + 50_000_000
    assert second == first
    assert route.call_count == 1


@respx.mock
async def test_cached_pvp_income_refetches_after_ttl(_main_module):
    route = respx.get(JOURNAL_URL).mock(return_value=Response(
        200, json=SAMPLE_JOURNAL, headers={"X-Pages": "1"},
    ))
    await _main_module._cached_pvp_income(CID, "tok")
    cached_at, amount = _main_module._pvp_income_cache[CID]
    _main_module._pvp_income_cache[CID] = (cached_at - 301, amount)
    again = await _main_module._cached_pvp_income(CID, "tok")
    assert again == amount
    assert route.call_count == 2


from db import upsert_token
from auth import make_session_cookie


async def _user_cookie(character_id: int = CID) -> dict:
    import time
    await upsert_token(
        character_id=character_id, character_name="Pilot",
        access_token="acc", refresh_token="ref",
        expires_at=time.time() + 1200, corporation_id=98340844,
    )
    return {"session": make_session_cookie(character_id)}


def _stub_all_other_member_endpoints():
    base = "https://esi.evetech.net/latest/characters"
    cb = f"{base}/{CID}"
    respx.get(f"{cb}/").mock(return_value=Response(200, json={
        "name": "Pilot", "corporation_id": 98340844, "security_status": 0.5,
    }))
    respx.get(f"{cb}/wallet/").mock(return_value=Response(200, json=0.0))
    respx.get(f"{cb}/skills/").mock(return_value=Response(200, json={
        "skills": [], "total_sp": 0, "unallocated_sp": 0,
    }))
    respx.get(f"{cb}/location/").mock(return_value=Response(200, json={}))
    respx.get(f"{cb}/online/").mock(return_value=Response(200, json={
        "online": False, "last_login": "2026-05-28T10:00:00Z",
        "last_logout": None, "logins": 42,
    }))
    respx.get(f"{cb}/ship/").mock(return_value=Response(200, json={}))
    respx.get(f"{cb}/attributes/").mock(return_value=Response(200, json={}))
    respx.get(f"{cb}/skillqueue/").mock(return_value=Response(200, json=[]))
    # /api/member's skills_summary also computes coverage which fans out to
    # /universe/groups/{id}/ once per skill-group id; stub the whole set.
    respx.get(
        url__regex=r"https://esi\.evetech\.net/latest/universe/groups/\d+/"
    ).mock(return_value=Response(200, json={"name": "Group", "types": []}))


@respx.mock
async def test_member_response_includes_pvp_income(client):
    _stub_all_other_member_endpoints()
    respx.get(JOURNAL_URL).mock(return_value=Response(
        200, json=SAMPLE_JOURNAL, headers={"X-Pages": "1"},
    ))
    cookies = await _user_cookie()
    r = await client.get("/api/member", cookies=cookies)
    assert r.status_code == 200
    body = r.json()
    assert body["pvp_income"]["data"] == 371_000_000 + 50_000_000
    assert body["pvp_income"]["error"] is None


@respx.mock
async def test_member_pvp_income_uses_cache_across_requests(client):
    _stub_all_other_member_endpoints()
    journal_route = respx.get(JOURNAL_URL).mock(return_value=Response(
        200, json=SAMPLE_JOURNAL, headers={"X-Pages": "1"},
    ))
    cookies = await _user_cookie()
    await client.get("/api/member", cookies=cookies)
    await client.get("/api/member", cookies=cookies)
    assert journal_route.call_count == 1  # second hit served from cache
