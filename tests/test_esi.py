import time
import pytest
import respx
from httpx import Response
from db import init_db, upsert_token
from esi import (
    get_valid_token, get_character, get_wallet, get_skills,
    get_corp_contracts, get_corp_projects,
    get_corp_structures, get_corp_starbases, get_starbase_detail,
    get_location_name, get_type_info, get_system_info, resolve_names,
    _decode_python_literal,
)


def test_decode_python_literal_normal():
    assert _decode_python_literal("My Ship") == "My Ship"


def test_decode_python_literal_unwraps():
    assert _decode_python_literal("u'\\u271a1'") == "✚1"
    assert _decode_python_literal('u"\\u271a Guardian"') == "✚ Guardian"


def test_decode_python_literal_safe_on_garbage():
    assert _decode_python_literal("u'broken") == "u'broken"
    assert _decode_python_literal(None) is None
    assert _decode_python_literal("") == ""


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
async def test_get_corp_projects():
    respx.get("https://esi.evetech.net/corporations/98340844/projects").mock(
        return_value=Response(200, json={
            "projects": [
                {"id": "abc-123", "name": "Deliver Tritanium", "state": "Active",
                 "last_modified": "2026-05-01T00:00:00Z",
                 "progress": {"current": 50, "desired": 100},
                 "reward": {"initial": 10000000.0, "remaining": 5000000.0}}
            ]
        })
    )
    result = await get_corp_projects(98340844, "acc_token")
    assert len(result) == 1
    assert result[0]["name"] == "Deliver Tritanium"
    assert result[0]["progress"]["current"] == 50


@respx.mock
async def test_resolve_names():
    respx.post("https://esi.evetech.net/latest/universe/names/").mock(
        return_value=Response(200, json=[
            {"id": 95465499, "name": "CCP Bartender", "category": "character"},
            {"id": 98340844, "name": "Grupa Operacyjna ZLY CHUJI", "category": "corporation"},
        ])
    )
    names = await resolve_names([95465499, 98340844, 0, None])
    assert names[95465499] == "CCP Bartender"
    assert names[98340844] == "Grupa Operacyjna ZLY CHUJI"


async def test_resolve_names_empty():
    assert await resolve_names([]) == {}
    assert await resolve_names([0, None]) == {}


@respx.mock
async def test_get_location_name_station():
    respx.get("https://esi.evetech.net/latest/universe/stations/60003760/").mock(
        return_value=Response(200, json={"station_id": 60003760,
            "name": "Jita IV - Moon 4 - Caldari Navy Assembly Plant"})
    )
    name = await get_location_name(60003760, "acc_token")
    assert "Jita" in name


@respx.mock
async def test_get_location_name_structure_caches():
    respx.get("https://esi.evetech.net/latest/universe/structures/1046603361682/").mock(
        return_value=Response(200, json={"name": "Amarr Assembly Plant"})
    )
    name = await get_location_name(1046603361682, "acc_token")
    assert name == "Amarr Assembly Plant"
    # Second call hits cache (no respx mock for second request).
    name2 = await get_location_name(1046603361682, "acc_token")
    assert name2 == "Amarr Assembly Plant"


@respx.mock
async def test_get_location_name_structure_fallback_label():
    respx.get("https://esi.evetech.net/latest/universe/structures/1099999999999/").mock(
        return_value=Response(403, json={"error": "Forbidden"})
    )
    name = await get_location_name(1099999999999, "acc_token")
    assert name.startswith("Citadel #")


@respx.mock
async def test_get_type_info():
    respx.get("https://esi.evetech.net/latest/universe/types/35832/").mock(
        return_value=Response(200, json={"name": "Astrahus", "group_id": 1657})
    )
    info = await get_type_info(35832)
    assert info["name"] == "Astrahus"
    assert info["group_id"] == 1657


@respx.mock
async def test_get_system_info():
    respx.get("https://esi.evetech.net/latest/universe/systems/30000142/").mock(
        return_value=Response(200, json={"name": "Jita", "security_status": 0.946})
    )
    info = await get_system_info(30000142)
    assert info["name"] == "Jita"
    assert info["security_status"] == pytest.approx(0.946)


@respx.mock
async def test_get_corp_structures():
    respx.get("https://esi.evetech.net/latest/corporations/98340844/structures/").mock(
        return_value=Response(200, json=[
            {"structure_id": 1046603361682, "type_id": 35832, "system_id": 30000142,
             "corporation_id": 98340844, "name": "ZLY CHUJI - HQ", "state": "shield_vulnerable",
             "profile_id": 1, "reinforce_hour": 18}
        ], headers={"X-Pages": "1"})
    )
    result = await get_corp_structures(98340844, "acc_token")
    assert len(result) == 1
    assert result[0]["type_id"] == 35832


@respx.mock
async def test_get_corp_starbases():
    respx.get("https://esi.evetech.net/latest/corporations/98340844/starbases/").mock(
        return_value=Response(200, json=[
            {"starbase_id": 1234567, "type_id": 12235, "system_id": 30000142, "state": "online"}
        ], headers={"X-Pages": "1"})
    )
    result = await get_corp_starbases(98340844, "acc_token")
    assert len(result) == 1
    assert result[0]["type_id"] == 12235


@respx.mock
async def test_get_starbase_detail():
    respx.get("https://esi.evetech.net/latest/corporations/98340844/starbases/1234567/").mock(
        return_value=Response(200, json={
            "allow_alliance_members": True, "allow_corporation_members": True,
            "anchor": "config_starbase_equipment_role",
            "attack_if_at_war": True, "attack_if_other_security_status_dropping": False,
            "fuel_bay_take": "config_starbase_equipment_role",
            "fuel_bay_view": "corporation_member",
            "offline": "config_starbase_equipment_role",
            "online": "config_starbase_equipment_role",
            "unanchor": "config_starbase_equipment_role",
            "use_alliance_standings": False,
            "fuels": [{"type_id": 4051, "quantity": 14400}],
        })
    )
    detail = await get_starbase_detail(98340844, 1234567, 30000142, "acc_token")
    assert detail["fuels"][0]["quantity"] == 14400
