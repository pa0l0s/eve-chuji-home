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
