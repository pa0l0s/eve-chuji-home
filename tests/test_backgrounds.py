import os

import pytest

from db import init_db

BACKGROUNDS_DIR = os.path.join(os.path.dirname(__file__), "..", "static", "backgrounds")


@pytest.fixture(autouse=True)
async def setup_db():
    await init_db()


async def test_backgrounds_returns_json_list(client):
    r = await client.get("/api/backgrounds")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) == 60


async def test_backgrounds_entries_are_real_image_files(client):
    r = await client.get("/api/backgrounds")
    for name in r.json():
        assert name.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
        assert os.path.isfile(os.path.join(BACKGROUNDS_DIR, name))


async def test_backgrounds_list_is_sorted(client):
    r = await client.get("/api/backgrounds")
    data = r.json()
    assert data == sorted(data)
