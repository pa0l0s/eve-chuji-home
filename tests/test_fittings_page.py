import time
import respx
from httpx import Response

from esi import (
    flag_to_slot_kind, eft_from_esi_fitting, parse_eft,
    list_character_fittings, create_character_fitting, delete_character_fitting,
    _fitting_meta_cache,
)
from db import init_db, upsert_token
from auth import make_session_cookie

import pytest


@pytest.fixture(autouse=True)
async def setup_db():
    await init_db()


async def _auth_cookie(character_id=123) -> dict:
    await upsert_token(
        character_id=character_id, character_name="Test Pilot",
        access_token="valid_acc", refresh_token="valid_ref",
        expires_at=time.time() + 1200, corporation_id=98340844,
    )
    return {"session": make_session_cookie(character_id)}


def test_flag_to_slot_kind_modules():
    assert flag_to_slot_kind("HiSlot0") == ("high", "module")
    assert flag_to_slot_kind("MedSlot3") == ("mid", "module")
    assert flag_to_slot_kind("LoSlot7") == ("low", "module")
    assert flag_to_slot_kind("RigSlot1") == ("rig", "module")
    assert flag_to_slot_kind("SubSystemSlot2") == ("subsystem", "module")


def test_flag_to_slot_kind_drone_and_cargo():
    assert flag_to_slot_kind("DroneBay") == (None, "drone")
    assert flag_to_slot_kind("Cargo") == (None, "charge")
    assert flag_to_slot_kind("FighterBay") == (None, "charge")


def test_eft_from_esi_fitting_groups_and_names():
    fitting = {
        "name": "My Rifter", "ship_type_id": 587,
        "items": [
            {"type_id": 100, "quantity": 1, "flag": "LoSlot0"},
            {"type_id": 200, "quantity": 1, "flag": "HiSlot0"},
            {"type_id": 300, "quantity": 5, "flag": "DroneBay"},
            {"type_id": 400, "quantity": 1000, "flag": "Cargo"},
        ],
    }
    name_map = {587: "Rifter", 100: "Damage Control II", 200: "125mm Gatling AutoCannon II",
                300: "Hobgoblin II", 400: "EMP S"}
    eft = eft_from_esi_fitting(fitting, name_map)
    assert eft.splitlines()[0] == "[Rifter, My Rifter]"
    assert "Damage Control II" in eft
    assert "125mm Gatling AutoCannon II" in eft
    assert "Hobgoblin II x5" in eft
    assert "EMP S x1000" in eft


def test_eft_from_esi_fitting_unknown_name_degrades():
    fitting = {"name": "F", "ship_type_id": 9, "items": [{"type_id": 8, "quantity": 1, "flag": "LoSlot0"}]}
    eft = eft_from_esi_fitting(fitting, {})
    assert eft.splitlines()[0] == "[Type #9, F]"
    assert "Type #8" in eft


def test_parse_eft_basic_groups_and_charge():
    text = (
        "[Rifter, My Rifter]\n"
        "Damage Control II\n"
        "\n"
        "200mm AutoCannon II, EMP S\n"
        "\n"
        "Small Projectile Burst Aerator I\n"
        "\n"
        "Hobgoblin II x5\n"
        "EMP S x1000\n"
    )
    p = parse_eft(text)
    assert p["ship_name"] == "Rifter"
    assert p["fit_name"] == "My Rifter"
    names = [l["name"] for l in p["lines"]]
    assert names == ["Damage Control II", "200mm AutoCannon II",
                     "Small Projectile Burst Aerator I", "Hobgoblin II", "EMP S"]
    ac = next(l for l in p["lines"] if l["name"] == "200mm AutoCannon II")
    assert ac["charge"] == "EMP S"
    drone = next(l for l in p["lines"] if l["name"] == "Hobgoblin II")
    assert drone["qty"] == 5


def test_parse_eft_skips_empty_slot_markers_and_blank_lines():
    text = "[Rifter, X]\n[Empty High slot]\n\nDamage Control II\n"
    p = parse_eft(text)
    assert [l["name"] for l in p["lines"]] == ["Damage Control II"]


def test_parse_eft_defaults_fit_name():
    p = parse_eft("[Rifter, ]\nDamage Control II")
    assert p["fit_name"] == "Imported Fit"


def test_parse_eft_rejects_missing_header():
    with pytest.raises(ValueError):
        parse_eft("Damage Control II\n200mm AutoCannon II")
    with pytest.raises(ValueError):
        parse_eft("")


def test_parse_eft_charge_with_quantity_strips_qty():
    p = parse_eft("[Rifter, X]\n200mm AutoCannon II, EMP S x10")
    line = p["lines"][0]
    assert line["name"] == "200mm AutoCannon II"
    assert line["charge"] == "EMP S"      # qty stripped from charge
    assert line["qty"] == 1


def test_parse_eft_zero_quantity_clamped_to_one():
    p = parse_eft("[Rifter, X]\nHobgoblin II x0")
    assert p["lines"][0]["qty"] == 1


def test_parse_eft_handles_crlf():
    p = parse_eft("[Rifter, X]\r\nDamage Control II\r\n")
    assert p["ship_name"] == "Rifter"
    assert [l["name"] for l in p["lines"]] == ["Damage Control II"]
