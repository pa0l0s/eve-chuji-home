import time
import pytest
import respx
from httpx import Response
from db import init_db, upsert_token
from esi import get_valid_token, get_character, get_wallet, get_skills, get_corp_contracts, get_location_name


@pytest.fixture(autouse=True)
async def setup_db():
    await init_db()


async def _insert_fresh_token(character_id=123):
    await upsert_token(
        character_id=character_id,
        character_name="Pilot",
        access_token="valid_acc",
        refresh_token="valid_ref",
        expires_at=time.time() + 1200,
        corporation_id=98340844,
    )


async def _insert_expired_token(character_id=123):
    await upsert_token(
        character_id=character_id,
        character_name="Pilot",
        access_token="expired_acc",
        refresh_token="valid_ref",
        expires_at=time.time() - 10,
        corporation_id=98340844,
    )


async def test_get_valid_token_fresh():
    await _insert_fresh_token()
    token = await get_valid_token(123)
    assert token == "valid_acc"


@respx.mock
async def test_get_valid_token_refreshes_when_expired():
    await _insert_expired_token()
    respx.post("https://login.eveonline.com/v2/oauth/token").mock(
        return_value=Response(200, json={
            "access_token": "new_acc",
            "refresh_token": "new_ref",
            "expires_in": 1200,
        })
    )
    token = await get_valid_token(123)
    assert token == "new_acc"


async def test_get_valid_token_raises_when_no_token():
    with pytest.raises(ValueError, match="No token found"):
        await get_valid_token(999)


@respx.mock
async def test_get_character():
    respx.get("https://esi.evetech.net/latest/characters/123/").mock(
        return_value=Response(200, json={
            "name": "Test Pilot",
            "corporation_id": 98340844,
            "security_status": 1.5,
        })
    )
    data = await get_character(123, "acc_token")
    assert data["name"] == "Test Pilot"


@respx.mock
async def test_get_wallet():
    respx.get("https://esi.evetech.net/latest/characters/123/wallet/").mock(
        return_value=Response(200, json=1500000.75)
    )
    balance = await get_wallet(123, "acc_token")
    assert balance == pytest.approx(1500000.75)


@respx.mock
async def test_get_skills():
    respx.get("https://esi.evetech.net/latest/characters/123/skills/").mock(
        return_value=Response(200, json={
            "total_sp": 50000000,
            "skills": [],
            "unallocated_sp": 0,
        })
    )
    data = await get_skills(123, "acc_token")
    assert data["total_sp"] == 50000000


@respx.mock
async def test_get_corp_contracts():
    respx.get("https://esi.evetech.net/latest/corporations/98340844/contracts/").mock(
        return_value=Response(200, json=[
            {"contract_id": 1, "type": "courier", "status": "outstanding",
             "start_location_id": 60003760, "end_location_id": 60003760,
             "volume": 10000.0, "reward": 5000000.0, "collateral": 0.0}
        ], headers={"X-Pages": "1"})
    )
    result = await get_corp_contracts(98340844, "acc_token")
    assert len(result) == 1
    assert result[0]["type"] == "courier"


@respx.mock
async def test_get_location_name_station():
    respx.get("https://esi.evetech.net/latest/universe/stations/60003760/").mock(
        return_value=Response(200, json={"station_id": 60003760,
            "name": "Jita IV - Moon 4 - Caldari Navy Assembly Plant"})
    )
    name = await get_location_name(60003760, "acc_token")
    assert "Jita" in name
