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
