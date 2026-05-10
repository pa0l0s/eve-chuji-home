import os
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport


@pytest.fixture(autouse=True)
def set_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("EVE_CLIENT_ID", "test_client_id")
    monkeypatch.setenv("EVE_CLIENT_SECRET", "test_client_secret")
    monkeypatch.setenv("EVE_CALLBACK_URL", "https://chuji.swoojeff.online/api/auth/callback")
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-32-chars-minimum!")
    monkeypatch.setenv("CORP_ID", "98340844")
    monkeypatch.setenv("JANICE_LINK_ID", "QcoH7M")


@pytest_asyncio.fixture
async def client(set_env):
    import importlib
    import main
    importlib.reload(main)
    from main import app
    from db import init_db
    await init_db()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
