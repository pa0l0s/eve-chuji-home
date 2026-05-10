# Chuji Homepage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a members-only EVE Online corporation portal with EVE SSO auth, a Corp Projects dashboard with Janice buy-back pricing, and a Member Profile page.

**Architecture:** FastAPI backend serving a single `static/index.html` SPA. JS calls `/api/auth/me` on load to determine auth state. EVE SSO OAuth2 with signed session cookies (`itsdangerous`). SQLite for refresh token storage and Janice price cache.

**Tech Stack:** FastAPI, aiosqlite, httpx, itsdangerous, python-dotenv; pytest + pytest-asyncio + respx for tests; `tiangolo/uvicorn-gunicorn-fastapi:python3.11` Docker image.

---

## File Map

| File | Responsibility |
|------|---------------|
| `requirements.txt` | Python dependencies |
| `pytest.ini` | pytest asyncio config |
| `prestart.sh` | Install requirements inside container before server starts |
| `db.py` | SQLite init + CRUD: `tokens` and `janice_cache` tables |
| `auth.py` | EVE SSO OAuth flow, signed session cookies, corp membership check |
| `esi.py` | ESI API client, automatic token refresh, type name resolution |
| `janice.py` | Janice appraisal API client (EVE paste format input), 1-hour SQLite price cache |
| `main.py` | FastAPI app: all routes, session dependency, StaticFiles mount |
| `static/index.html` | Complete frontend SPA (login, projects, member views) |
| `docker-compose.yml` | Container config, NAS volume mount, port 8760 |
| `tests/conftest.py` | Shared pytest fixtures (temp DB, env vars, test client) |
| `tests/test_db.py` | DB layer unit tests |
| `tests/test_auth.py` | Auth helpers unit tests |
| `tests/test_esi.py` | ESI client unit tests (mocked HTTP) |
| `tests/test_janice.py` | Janice client unit tests (mocked HTTP) |
| `tests/test_api.py` | FastAPI endpoint integration tests |

---

### Task 1: Scaffolding

**Files:**
- Create: `requirements.txt`
- Create: `pytest.ini`
- Create: `prestart.sh`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: Create `requirements.txt`**

```
fastapi
httpx
aiosqlite
itsdangerous
python-dotenv
pytest
pytest-asyncio
respx
```

- [ ] **Step 2: Create `pytest.ini`**

```ini
[pytest]
asyncio_mode = auto
```

- [ ] **Step 3: Create `prestart.sh`** (runs inside container before server starts)

```bash
#!/bin/bash
pip install -r /app/requirements.txt
```

Make it executable:
```bash
chmod +x prestart.sh
```

- [ ] **Step 4: Create `tests/__init__.py`**

Empty file:
```python
```

- [ ] **Step 5: Create `tests/conftest.py`**

```python
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
```

- [ ] **Step 6: Commit**

```bash
git add requirements.txt pytest.ini prestart.sh tests/
git commit -m "feat: project scaffolding and test fixtures"
```

---

### Task 2: Database Layer

**Files:**
- Create: `db.py`
- Create: `tests/test_db.py`

- [ ] **Step 1: Write `tests/test_db.py`**

```python
import time
import pytest
from db import init_db, upsert_token, get_token, delete_token, upsert_janice_cache, get_janice_cache


async def test_init_db_creates_tables():
    await init_db()


async def test_upsert_and_get_token():
    await init_db()
    await upsert_token(
        character_id=123,
        character_name="Test Pilot",
        access_token="acc_token",
        refresh_token="ref_token",
        expires_at=time.time() + 1200,
        corporation_id=98340844,
    )
    row = await get_token(123)
    assert row["character_name"] == "Test Pilot"
    assert row["corporation_id"] == 98340844


async def test_get_token_returns_none_for_missing():
    await init_db()
    assert await get_token(999) is None


async def test_delete_token():
    await init_db()
    await upsert_token(123, "Pilot", "a", "r", time.time() + 1200, 98340844)
    await delete_token(123)
    assert await get_token(123) is None


async def test_upsert_overwrites_token():
    await init_db()
    await upsert_token(123, "Pilot", "old_acc", "old_ref", time.time() + 1200, 98340844)
    await upsert_token(123, "Pilot", "new_acc", "new_ref", time.time() + 1200, 98340844)
    row = await get_token(123)
    assert row["access_token"] == "new_acc"


async def test_upsert_and_get_janice_cache():
    await init_db()
    await upsert_janice_cache(item_id=34, buy_price=4.5, cached_at=time.time())
    row = await get_janice_cache(34)
    assert row["buy_price"] == pytest.approx(4.5)


async def test_get_janice_cache_returns_none_for_missing():
    await init_db()
    assert await get_janice_cache(9999) is None
```

- [ ] **Step 2: Run tests — expect FAIL (db module missing)**

```bash
pytest tests/test_db.py -v
```
Expected: `ModuleNotFoundError: No module named 'db'`

- [ ] **Step 3: Create `db.py`**

```python
import os
import time
import aiosqlite
from pathlib import Path


def _db_path() -> str:
    url = os.getenv("DATABASE_URL", "sqlite:///./data/chuji.db")
    return url.replace("sqlite:///", "")


async def init_db():
    path = _db_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(path) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tokens (
                character_id   INTEGER PRIMARY KEY,
                character_name TEXT    NOT NULL,
                access_token   TEXT    NOT NULL,
                refresh_token  TEXT    NOT NULL,
                expires_at     REAL    NOT NULL,
                corporation_id INTEGER NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS janice_cache (
                item_id   INTEGER PRIMARY KEY,
                buy_price REAL    NOT NULL,
                cached_at REAL    NOT NULL
            )
        """)
        await db.commit()


async def upsert_token(character_id, character_name, access_token, refresh_token, expires_at, corporation_id):
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute("""
            INSERT INTO tokens (character_id, character_name, access_token, refresh_token, expires_at, corporation_id)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(character_id) DO UPDATE SET
                character_name = excluded.character_name,
                access_token   = excluded.access_token,
                refresh_token  = excluded.refresh_token,
                expires_at     = excluded.expires_at,
                corporation_id = excluded.corporation_id
        """, (character_id, character_name, access_token, refresh_token, expires_at, corporation_id))
        await db.commit()


async def get_token(character_id: int) -> dict | None:
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM tokens WHERE character_id = ?", (character_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def delete_token(character_id: int):
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute("DELETE FROM tokens WHERE character_id = ?", (character_id,))
        await db.commit()


async def upsert_janice_cache(item_id: int, buy_price: float, cached_at: float):
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute("""
            INSERT INTO janice_cache (item_id, buy_price, cached_at)
            VALUES (?, ?, ?)
            ON CONFLICT(item_id) DO UPDATE SET
                buy_price = excluded.buy_price,
                cached_at = excluded.cached_at
        """, (item_id, buy_price, cached_at))
        await db.commit()


async def get_janice_cache(item_id: int) -> dict | None:
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM janice_cache WHERE item_id = ?", (item_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None
```

- [ ] **Step 4: Install deps and run tests — expect PASS**

```bash
pip install -r requirements.txt
pytest tests/test_db.py -v
```
Expected: 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add db.py tests/test_db.py
git commit -m "feat: SQLite database layer with tokens and janice_cache tables"
```

---

### Task 3: Auth Module

**Files:**
- Create: `auth.py`
- Create: `tests/test_auth.py`

- [ ] **Step 1: Write `tests/test_auth.py`**

```python
import pytest
import respx
from httpx import Response
from auth import (
    build_login_url,
    exchange_code,
    make_session_cookie,
    read_session_cookie,
    verify_token,
)


def test_build_login_url_contains_client_id():
    url = build_login_url("test_state_123")
    assert "test_client_id" in url
    assert "test_state_123" in url
    assert "login.eveonline.com" in url


def test_session_cookie_roundtrip():
    cookie = make_session_cookie(12345)
    assert read_session_cookie(cookie) == 12345


def test_read_session_cookie_invalid_returns_none():
    assert read_session_cookie("garbage.cookie.value") is None


def test_read_session_cookie_empty_returns_none():
    assert read_session_cookie("") is None


@respx.mock
async def test_exchange_code_returns_tokens():
    respx.post("https://login.eveonline.com/v2/oauth/token").mock(
        return_value=Response(200, json={
            "access_token": "acc_abc",
            "refresh_token": "ref_abc",
            "expires_in": 1200,
        })
    )
    result = await exchange_code("auth_code_xyz")
    assert result["access_token"] == "acc_abc"
    assert result["refresh_token"] == "ref_abc"


@respx.mock
async def test_verify_token_returns_character_info():
    respx.get("https://esi.evetech.net/verify/").mock(
        return_value=Response(200, json={
            "CharacterID": 9001,
            "CharacterName": "Test Pilot",
        })
    )
    result = await verify_token("acc_abc")
    assert result["CharacterID"] == 9001
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
pytest tests/test_auth.py -v
```
Expected: `ModuleNotFoundError: No module named 'auth'`

- [ ] **Step 3: Create `auth.py`**

```python
import os
import base64
from urllib.parse import urlencode

import httpx
from itsdangerous import URLSafeSerializer, BadSignature

EVE_SSO_AUTH_URL = "https://login.eveonline.com/v2/oauth/authorize"
EVE_SSO_TOKEN_URL = "https://login.eveonline.com/v2/oauth/token"
ESI_VERIFY_URL = "https://esi.evetech.net/verify/"

SCOPES = " ".join([
    "publicData",
    "esi-corporations.read_projects.v1",
    "esi-wallet.read_character_wallet.v1",
    "esi-skills.read_skills.v1",
    "esi-characters.read_corporation_roles.v1",
])


def _signer() -> URLSafeSerializer:
    secret = os.getenv("SECRET_KEY", "dev-insecure-key")
    return URLSafeSerializer(secret)


def build_login_url(state: str) -> str:
    params = {
        "response_type": "code",
        "redirect_uri": os.getenv("EVE_CALLBACK_URL"),
        "client_id": os.getenv("EVE_CLIENT_ID"),
        "scope": SCOPES,
        "state": state,
    }
    return f"{EVE_SSO_AUTH_URL}?{urlencode(params)}"


def _credentials() -> str:
    client_id = os.getenv("EVE_CLIENT_ID")
    client_secret = os.getenv("EVE_CLIENT_SECRET")
    return base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()


async def exchange_code(code: str) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.post(
            EVE_SSO_TOKEN_URL,
            headers={
                "Authorization": f"Basic {_credentials()}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"grant_type": "authorization_code", "code": code},
        )
        r.raise_for_status()
        return r.json()


async def verify_token(access_token: str) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.get(
            ESI_VERIFY_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        r.raise_for_status()
        return r.json()


def make_session_cookie(character_id: int) -> str:
    return _signer().dumps({"cid": character_id})


def read_session_cookie(cookie: str) -> int | None:
    if not cookie:
        return None
    try:
        data = _signer().loads(cookie)
        return data["cid"]
    except (BadSignature, KeyError, Exception):
        return None
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
pytest tests/test_auth.py -v
```
Expected: 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add auth.py tests/test_auth.py
git commit -m "feat: EVE SSO auth module with session cookie signing"
```

---

### Task 4: ESI Client

**Files:**
- Create: `esi.py`
- Create: `tests/test_esi.py`

- [ ] **Step 1: Write `tests/test_esi.py`**

```python
import time
import pytest
import respx
from httpx import Response
from db import init_db, upsert_token
from esi import get_valid_token, get_character, get_wallet, get_skills, get_corp_projects


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
async def test_get_corp_projects():
    respx.get("https://esi.evetech.net/latest/corporations/98340844/projects/").mock(
        return_value=Response(200, json=[
            {"project_id": 1, "name": "Test Project", "status": "in_progress"}
        ])
    )
    projects = await get_corp_projects(98340844, "acc_token")
    assert len(projects) == 1
    assert projects[0]["name"] == "Test Project"
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
pytest tests/test_esi.py -v
```
Expected: `ModuleNotFoundError: No module named 'esi'`

- [ ] **Step 3: Create `esi.py`**

```python
import os
import time
import base64
import httpx

from db import get_token, upsert_token

ESI_BASE = "https://esi.evetech.net/latest"
EVE_SSO_TOKEN_URL = "https://login.eveonline.com/v2/oauth/token"


def _credentials() -> str:
    cid = os.getenv("EVE_CLIENT_ID")
    secret = os.getenv("EVE_CLIENT_SECRET")
    return base64.b64encode(f"{cid}:{secret}".encode()).decode()


async def get_valid_token(character_id: int) -> str:
    row = await get_token(character_id)
    if not row:
        raise ValueError("No token found")
    if time.time() >= row["expires_at"] - 60:
        row = await _refresh(row)
    return row["access_token"]


async def _refresh(row: dict) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.post(
            EVE_SSO_TOKEN_URL,
            headers={
                "Authorization": f"Basic {_credentials()}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"grant_type": "refresh_token", "refresh_token": row["refresh_token"]},
        )
        r.raise_for_status()
        data = r.json()

    updated = {
        **row,
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token", row["refresh_token"]),
        "expires_at": time.time() + data["expires_in"],
    }
    await upsert_token(
        updated["character_id"], updated["character_name"],
        updated["access_token"], updated["refresh_token"],
        updated["expires_at"], updated["corporation_id"],
    )
    return updated


async def get_character(character_id: int, access_token: str) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{ESI_BASE}/characters/{character_id}/",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        r.raise_for_status()
        return r.json()


async def get_wallet(character_id: int, access_token: str) -> float:
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{ESI_BASE}/characters/{character_id}/wallet/",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        r.raise_for_status()
        return r.json()


async def get_skills(character_id: int, access_token: str) -> dict:
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{ESI_BASE}/characters/{character_id}/skills/",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        r.raise_for_status()
        return r.json()


async def get_corp_projects(corporation_id: int, access_token: str) -> list:
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{ESI_BASE}/corporations/{corporation_id}/projects/",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        r.raise_for_status()
        return r.json()
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
pytest tests/test_esi.py -v
```
Expected: 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add esi.py tests/test_esi.py
git commit -m "feat: ESI API client with automatic token refresh"
```

---

### Task 5: Janice Client

**Files:**
- Modify: `esi.py` — add `get_type_name(type_id)` function
- Modify: `tests/test_esi.py` — add test for `get_type_name`
- Create: `janice.py`
- Create: `tests/test_janice.py`

> **Janice API note:** The Janice appraisal endpoint accepts a plain-text EVE paste list (item names × quantities) and returns JSON with per-item prices. Type IDs from ESI must first be resolved to item names via the ESI universe types endpoint. The `janice_cache` table stores prices keyed by `type_id`.

- [ ] **Step 1: Add `get_type_name` to `esi.py`**

Append to `esi.py`:

```python
async def get_type_name(type_id: int) -> str:
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{ESI_BASE}/universe/types/{type_id}/")
        r.raise_for_status()
        return r.json().get("name", str(type_id))
```

- [ ] **Step 2: Add test for `get_type_name` to `tests/test_esi.py`**

Append to `tests/test_esi.py`:

```python
@respx.mock
async def test_get_type_name():
    respx.get("https://esi.evetech.net/latest/universe/types/34/").mock(
        return_value=Response(200, json={"type_id": 34, "name": "Tritanium"})
    )
    name = await get_type_name(34)
    assert name == "Tritanium"
```

Also add `get_type_name` to the import line at the top of `tests/test_esi.py`:
```python
from esi import get_valid_token, get_character, get_wallet, get_skills, get_corp_projects, get_type_name
```

- [ ] **Step 3: Run ESI tests — expect PASS**

```bash
pytest tests/test_esi.py -v
```
Expected: 8 tests PASS

- [ ] **Step 4: Write `tests/test_janice.py`**

```python
import time
import pytest
import respx
from httpx import Response
from db import init_db, upsert_janice_cache
from janice import get_prices_for_items


JANICE_URL = "https://janice.e-351.com/api/rest/v2/appraisal"


@pytest.fixture(autouse=True)
async def setup_db():
    await init_db()


@respx.mock
async def test_fetches_prices_from_api():
    respx.post(JANICE_URL).mock(
        return_value=Response(200, json={
            "items": [
                {"typeID": 34, "name": "Tritanium", "prices": {"buy": {"fivePercent": 5.0}}},
                {"typeID": 35, "name": "Pyerite",   "prices": {"buy": {"fivePercent": 10.0}}},
            ]
        })
    )
    # Pass {type_id: name} mapping
    prices = await get_prices_for_items({34: "Tritanium", 35: "Pyerite"})
    assert prices[34] == pytest.approx(5.0 * 0.90)
    assert prices[35] == pytest.approx(10.0 * 0.90)


@respx.mock
async def test_uses_cache_when_fresh():
    await upsert_janice_cache(item_id=34, buy_price=4.5, cached_at=time.time())
    # API should NOT be called — 34 is cached, nothing to fetch
    respx.post(JANICE_URL).mock(return_value=Response(500))
    prices = await get_prices_for_items({34: "Tritanium"})
    assert prices[34] == pytest.approx(4.5)


@respx.mock
async def test_refetches_when_cache_expired():
    await upsert_janice_cache(item_id=34, buy_price=1.0, cached_at=time.time() - 7200)
    respx.post(JANICE_URL).mock(
        return_value=Response(200, json={
            "items": [
                {"typeID": 34, "name": "Tritanium", "prices": {"buy": {"fivePercent": 5.0}}},
            ]
        })
    )
    prices = await get_prices_for_items({34: "Tritanium"})
    assert prices[34] == pytest.approx(5.0 * 0.90)


@respx.mock
async def test_returns_empty_dict_on_api_error():
    respx.post(JANICE_URL).mock(return_value=Response(503))
    prices = await get_prices_for_items({34: "Tritanium", 35: "Pyerite"})
    assert prices == {}
```

- [ ] **Step 5: Run tests — expect FAIL**

```bash
pytest tests/test_janice.py -v
```
Expected: `ModuleNotFoundError: No module named 'janice'`

- [ ] **Step 6: Create `janice.py`**

```python
import time
import httpx

from db import get_janice_cache, upsert_janice_cache

# Janice appraisal API — accepts EVE paste text, returns item prices
# Response schema assumption: {"items": [{"typeID": int, "name": str, "prices": {"buy": {"fivePercent": float}}}]}
# Verify against current Janice API if the integration fails.
JANICE_APPRAISAL_URL = "https://janice.e-351.com/api/rest/v2/appraisal"
CACHE_TTL = 3600  # 1 hour


async def get_prices_for_items(type_id_to_name: dict[int, str]) -> dict[int, float]:
    """Accepts {type_id: item_name} mapping, returns {type_id: corp_buy_price (90% Jita)}."""
    result: dict[int, float] = {}
    to_fetch: dict[int, str] = {}

    for type_id, name in type_id_to_name.items():
        cached = await get_janice_cache(type_id)
        if cached and time.time() - cached["cached_at"] < CACHE_TTL:
            result[type_id] = cached["buy_price"]
        else:
            to_fetch[type_id] = name

    if to_fetch:
        fetched = await _fetch_prices(to_fetch)
        now = time.time()
        for type_id, price in fetched.items():
            await upsert_janice_cache(type_id, price, now)
            result[type_id] = price

    return result


async def _fetch_prices(type_id_to_name: dict[int, str]) -> dict[int, float]:
    # Build EVE paste-format text: "Item Name x 1" per line
    paste_text = "\n".join(f"{name} x 1" for name in type_id_to_name.values())
    name_to_type_id = {v: k for k, v in type_id_to_name.items()}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                JANICE_APPRAISAL_URL,
                params={"market": "jita", "persist": "false", "compactize": "true"},
                content=paste_text,
                headers={"Content-Type": "text/plain"},
            )
            r.raise_for_status()
            data = r.json()

        result: dict[int, float] = {}
        for item in data.get("items", []):
            name = item.get("name")
            type_id = name_to_type_id.get(name) or item.get("typeID")
            buy_five_pct = item.get("prices", {}).get("buy", {}).get("fivePercent", 0)
            if type_id and buy_five_pct:
                result[type_id] = buy_five_pct * 0.90
        return result
    except Exception:
        return {}
```

- [ ] **Step 7: Run tests — expect PASS**

```bash
pytest tests/test_janice.py -v
```
Expected: 4 tests PASS

- [ ] **Step 8: Commit**

```bash
git add janice.py esi.py tests/test_janice.py tests/test_esi.py
git commit -m "feat: Janice appraisal client with name-based lookup and 1-hour price cache"
```

---

### Task 6: FastAPI App

**Files:**
- Create: `main.py`
- Create: `tests/test_api.py`

- [ ] **Step 1: Write `tests/test_api.py`**

```python
import time
import pytest
from db import init_db, upsert_token
from auth import make_session_cookie


@pytest.fixture(autouse=True)
async def setup_db():
    await init_db()


async def _auth_cookie(character_id=123) -> dict:
    await upsert_token(
        character_id=character_id,
        character_name="Test Pilot",
        access_token="valid_acc",
        refresh_token="valid_ref",
        expires_at=time.time() + 1200,
        corporation_id=98340844,
    )
    return {"session": make_session_cookie(character_id)}


async def test_root_returns_html(client):
    r = await client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


async def test_auth_me_returns_401_without_session(client):
    r = await client.get("/api/auth/me")
    assert r.status_code == 401


async def test_auth_me_returns_character_info(client):
    cookies = await _auth_cookie()
    r = await client.get("/api/auth/me", cookies=cookies)
    assert r.status_code == 200
    data = r.json()
    assert data["character_id"] == 123
    assert data["character_name"] == "Test Pilot"


async def test_auth_login_redirects_to_eve_sso(client):
    r = await client.get("/api/auth/login", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert "login.eveonline.com" in r.headers["location"]


async def test_auth_logout_clears_session(client):
    cookies = await _auth_cookie()
    r = await client.get("/api/auth/logout", cookies=cookies, follow_redirects=False)
    assert r.status_code in (302, 307)
    # session cookie should be cleared (empty value or deleted)
    assert "session" not in r.cookies or r.cookies["session"] == ""


async def test_projects_returns_401_without_auth(client):
    r = await client.get("/api/projects")
    assert r.status_code == 401


async def test_member_returns_401_without_auth(client):
    r = await client.get("/api/member")
    assert r.status_code == 401
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
pytest tests/test_api.py -v
```
Expected: `ModuleNotFoundError: No module named 'main'`

- [ ] **Step 3: Create `main.py`**

```python
import os
import secrets
import asyncio
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Cookie, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import db as database
from auth import build_login_url, exchange_code, verify_token, make_session_cookie, read_session_cookie
from esi import get_valid_token, get_character, get_wallet, get_skills, get_corp_projects, get_type_name
from janice import get_prices_for_items

CORP_ID = int(os.getenv("CORP_ID", "0"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    await database.init_db()
    yield


app = FastAPI(lifespan=lifespan)


async def get_current_character_id(session: str | None = Cookie(None)) -> int:
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    character_id = read_session_cookie(session)
    if not character_id:
        raise HTTPException(status_code=401, detail="Invalid session")
    return character_id


# ── Auth routes ──────────────────────────────────────────────────────────────

@app.get("/api/auth/login")
async def login(response: RedirectResponse = None):
    state = secrets.token_urlsafe(16)
    url = build_login_url(state)
    r = RedirectResponse(url=url)
    r.set_cookie("oauth_state", state, httponly=True, samesite="lax", max_age=300)
    return r


@app.get("/api/auth/callback")
async def callback(code: str, state: str, request: Request):
    stored_state = request.cookies.get("oauth_state")
    if not stored_state or stored_state != state:
        raise HTTPException(status_code=400, detail="Invalid OAuth state")

    try:
        tokens = await exchange_code(code)
        char_info = await verify_token(tokens["access_token"])
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="EVE SSO error")

    character_id = char_info["CharacterID"]
    character_name = char_info["CharacterName"]

    # Fetch corporation_id from ESI
    try:
        esi_char = await get_character(character_id, tokens["access_token"])
        corporation_id = esi_char.get("corporation_id", 0)
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="ESI error")

    if corporation_id != CORP_ID:
        raise HTTPException(status_code=403, detail="Not a member of Grupa Operacyjna ZLY CHUJI")

    import time
    await database.upsert_token(
        character_id=character_id,
        character_name=character_name,
        access_token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
        expires_at=time.time() + tokens.get("expires_in", 1200),
        corporation_id=corporation_id,
    )

    resp = RedirectResponse(url="/")
    resp.set_cookie("session", make_session_cookie(character_id), httponly=True, samesite="lax")
    resp.delete_cookie("oauth_state")
    return resp


@app.get("/api/auth/me")
async def me(session: str | None = Cookie(None)):
    character_id = await get_current_character_id(session)
    row = await database.get_token(character_id)
    if not row:
        raise HTTPException(status_code=401, detail="Session expired")
    return {
        "character_id": row["character_id"],
        "character_name": row["character_name"],
        "corporation_id": row["corporation_id"],
    }


@app.get("/api/auth/logout")
async def logout():
    resp = RedirectResponse(url="/")
    resp.delete_cookie("session")
    return resp


# ── Data routes ──────────────────────────────────────────────────────────────

@app.get("/api/projects")
async def projects(session: str | None = Cookie(None)):
    character_id = await get_current_character_id(session)
    try:
        access_token = await get_valid_token(character_id)
        raw_projects = await get_corp_projects(CORP_ID, access_token)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 403:
            raise HTTPException(status_code=403, detail="Insufficient corporation roles")
        raise HTTPException(status_code=502, detail="ESI unavailable")
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="ESI unavailable")

    # Filter to active projects only
    # NOTE: Verify "status" field value from ESI — may be "in_progress", "active", etc.
    active = [p for p in raw_projects if p.get("status") not in ("completed", "cancelled")]

    # Collect type_ids from buyback projects, resolve names, fetch Janice prices
    buyback_type_ids: list[int] = []
    for project in active:
        if project.get("type") == "Buyback":
            for item in project.get("required_deliverables", []):
                buyback_type_ids.append(item["type_id"])

    prices: dict[int, float] = {}
    if buyback_type_ids:
        unique_ids = list(set(buyback_type_ids))
        names = await asyncio.gather(*[get_type_name(tid) for tid in unique_ids])
        type_id_to_name = dict(zip(unique_ids, names))
        prices = await get_prices_for_items(type_id_to_name)

    # Attach price data to items
    for project in active:
        for item in project.get("required_deliverables", []):
            tid = item["type_id"]
            item["corp_buy_price"] = prices.get(tid)

    return active


@app.get("/api/member")
async def member(session: str | None = Cookie(None)):
    character_id = await get_current_character_id(session)
    try:
        access_token = await get_valid_token(character_id)
        char_data, wallet, skills = await asyncio.gather(
            get_character(character_id, access_token),
            get_wallet(character_id, access_token),
            get_skills(character_id, access_token),
        )
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="ESI unavailable")

    skill_queue_active = bool(skills.get("skills") and any(
        s.get("active_skill_level", 0) < s.get("trained_skill_level", 0)
        for s in skills.get("skills", [])
    ))

    return {
        "character_id": character_id,
        "character_name": char_data.get("name"),
        "corporation_id": char_data.get("corporation_id"),
        "security_status": char_data.get("security_status", 0),
        "wallet_balance": wallet,
        "total_sp": skills.get("total_sp", 0),
        "training_active": skill_queue_active,
    }


# ── Static files (must be last) ───────────────────────────────────────────────

app.mount("/", StaticFiles(directory="static", html=True), name="static")
```

- [ ] **Step 4: Create `static/` directory placeholder so StaticFiles doesn't crash during tests**

```bash
mkdir -p static
touch static/index.html
```

Add to `static/index.html`:
```html
<!DOCTYPE html><html><body>placeholder</body></html>
```

- [ ] **Step 5: Run tests — expect PASS**

```bash
pytest tests/test_api.py -v
```
Expected: 7 tests PASS

- [ ] **Step 6: Run full test suite**

```bash
pytest -v
```
Expected: all tests PASS

- [ ] **Step 7: Commit**

```bash
git add main.py static/index.html tests/test_api.py
git commit -m "feat: FastAPI app with auth, projects, and member API endpoints"
```

---

### Task 7: Frontend

**Files:**
- Modify: `static/index.html`

> Build the complete SPA. Three views: login, projects dashboard, member profile. Design tokens from shelly-plug reference project.

- [ ] **Step 1: Replace `static/index.html` with the complete frontend**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Grupa Operacyjna ZLY CHUJI</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      background: #0f1117;
      color: #e2e8f0;
      min-height: 100vh;
    }

    /* ── Nav ── */
    nav {
      background: #1e2130;
      border-bottom: 1px solid #2d3348;
      padding: 0.75rem 2rem;
      display: flex;
      align-items: center;
      justify-content: space-between;
    }

    .nav-corp { font-weight: 700; font-size: 1rem; color: #f8fafc; letter-spacing: 0.03em; }

    .nav-links { display: flex; gap: 0.25rem; }

    .nav-btn {
      background: none;
      border: none;
      color: #64748b;
      font-size: 0.75rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      padding: 0.4rem 0.75rem;
      border-radius: 6px;
      cursor: pointer;
      transition: color 0.15s, background 0.15s;
    }

    .nav-btn:hover { color: #e2e8f0; background: #252a3d; }
    .nav-btn.active { color: #38bdf8; background: #0c2233; }

    .nav-right { display: flex; align-items: center; gap: 0.75rem; }

    .nav-char { display: flex; align-items: center; gap: 0.5rem; font-size: 0.85rem; color: #94a3b8; }

    .nav-portrait {
      width: 32px; height: 32px;
      border-radius: 50%;
      border: 1px solid #2d3348;
      background: #252a3d;
    }

    .btn-logout {
      background: none;
      border: 1px solid #2d3348;
      color: #64748b;
      font-size: 0.7rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      padding: 0.3rem 0.7rem;
      border-radius: 6px;
      cursor: pointer;
      transition: color 0.15s, border-color 0.15s;
    }

    .btn-logout:hover { color: #f87171; border-color: #f87171; }

    /* ── Layout ── */
    main { padding: 2rem; }

    .layout {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
      gap: 1.25rem;
      max-width: 1200px;
      margin: 0 auto;
    }

    .page-title {
      font-size: 1.1rem;
      font-weight: 600;
      color: #f8fafc;
      margin-bottom: 1.5rem;
      max-width: 1200px;
      margin-left: auto;
      margin-right: auto;
    }

    /* ── Cards ── */
    .card {
      background: #1e2130;
      border: 1px solid #2d3348;
      border-radius: 12px;
      padding: 1.5rem;
    }

    .card.full { grid-column: 1 / -1; }

    .card-title {
      font-size: 0.75rem;
      font-weight: 600;
      color: #64748b;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      margin-bottom: 1rem;
    }

    /* ── Metric ── */
    .metric {
      background: #252a3d;
      border-radius: 8px;
      padding: 0.9rem 1rem;
    }

    .metric .label {
      font-size: 0.7rem;
      color: #475569;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      margin-bottom: 0.3rem;
    }

    .metric .val { font-size: 1.6rem; font-weight: 700; color: #38bdf8; }
    .metric .unit { font-size: 0.8rem; color: #475569; margin-left: 0.2rem; }

    /* ── Progress ── */
    .progress-wrap { margin-top: 0.5rem; }

    .progress-label {
      display: flex;
      justify-content: space-between;
      font-size: 0.75rem;
      color: #64748b;
      margin-bottom: 0.35rem;
    }

    .progress-bar {
      height: 6px;
      background: #252a3d;
      border-radius: 3px;
      overflow: hidden;
    }

    .progress-fill {
      height: 100%;
      background: #38bdf8;
      border-radius: 3px;
      transition: width 0.4s ease;
    }

    /* ── Badge ── */
    .badge {
      display: inline-block;
      border-radius: 4px;
      padding: 0.1rem 0.45rem;
      font-size: 0.65rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }

    .badge-blue   { background: #0c2233; color: #38bdf8; }
    .badge-green  { background: #052e16; color: #4ade80; }
    .badge-muted  { background: #1e2130; color: #475569; border: 1px solid #2d3348; }
    .badge-orange { background: #2d1a00; color: #fb923c; }

    /* ── Table ── */
    table { width: 100%; border-collapse: collapse; font-size: 0.82rem; margin-top: 0.75rem; }

    th {
      text-align: left;
      padding: 0.4rem 0.6rem;
      color: #475569;
      font-weight: 500;
      font-size: 0.72rem;
      border-bottom: 1px solid #2d3348;
    }

    td { padding: 0.6rem 0.6rem; border-bottom: 1px solid #1a1e2e; color: #94a3b8; }
    tr:last-child td { border-bottom: none; }

    /* ── Character card ── */
    .char-card {
      display: flex;
      align-items: center;
      gap: 1.25rem;
    }

    .char-portrait {
      width: 80px;
      height: 80px;
      border-radius: 8px;
      border: 1px solid #2d3348;
      background: #252a3d;
    }

    .char-info .name { font-size: 1.1rem; font-weight: 700; color: #f8fafc; }
    .char-info .corp { font-size: 0.8rem; color: #64748b; margin-top: 0.2rem; }

    .sec-status {
      font-size: 0.75rem;
      font-weight: 700;
      margin-top: 0.4rem;
    }

    /* ── Metrics grid ── */
    .metrics { display: grid; grid-template-columns: 1fr 1fr; gap: 0.75rem; margin-top: 1rem; }

    /* ── Banner ── */
    .banner {
      background: #450a0a;
      border: 1px solid #ef4444;
      border-radius: 8px;
      padding: 0.6rem 1rem;
      margin-bottom: 1.25rem;
      color: #fca5a5;
      font-size: 0.8rem;
      max-width: 1200px;
      margin-left: auto;
      margin-right: auto;
      display: none;
    }

    .banner.show { display: block; }

    /* ── Login view ── */
    #view-login {
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
    }

    .login-card {
      background: #1e2130;
      border: 1px solid #2d3348;
      border-radius: 16px;
      padding: 3rem 2.5rem;
      text-align: center;
      max-width: 400px;
      width: 100%;
    }

    .login-corp {
      font-size: 1.3rem;
      font-weight: 700;
      color: #f8fafc;
      margin-bottom: 0.4rem;
    }

    .login-sub {
      font-size: 0.8rem;
      color: #475569;
      margin-bottom: 2rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }

    .login-btn {
      display: inline-block;
      margin-top: 0.5rem;
    }

    .login-btn img {
      height: 38px;
      transition: opacity 0.15s;
    }

    .login-btn:hover img { opacity: 0.85; }

    /* ── Spinner ── */
    .spinner {
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      color: #475569;
      font-size: 0.85rem;
    }

    /* ── Hidden ── */
    .hidden { display: none !important; }
  </style>
</head>
<body>

<!-- Loading spinner -->
<div id="view-loading" class="spinner">Loading…</div>

<!-- Login view -->
<div id="view-login" class="hidden">
  <div class="login-card">
    <div class="login-corp">Grupa Operacyjna</div>
    <div class="login-corp" style="color:#38bdf8">ZLY CHUJI</div>
    <div class="login-sub">Corporation Portal · Members Only</div>
    <a href="/api/auth/login" class="login-btn">
      <img src="https://web.ccpgames.com/eve-sso/img/eve-sso-login-black-small.png" alt="Login with EVE Online">
    </a>
  </div>
</div>

<!-- App shell (shown when authenticated) -->
<div id="view-app" class="hidden">

  <nav>
    <span class="nav-corp">ZLY CHUJI</span>
    <div class="nav-links">
      <button class="nav-btn active" id="btn-projects" onclick="showView('projects')">Projects</button>
      <button class="nav-btn" id="btn-member" onclick="showView('member')">Member</button>
    </div>
    <div class="nav-right">
      <div class="nav-char">
        <img id="nav-portrait" class="nav-portrait" src="" alt="">
        <span id="nav-name"></span>
      </div>
      <a href="/api/auth/logout"><button class="btn-logout">Logout</button></a>
    </div>
  </nav>

  <main>
    <!-- Projects view -->
    <div id="view-projects">
      <div id="projects-error" class="banner"></div>
      <div class="page-title" style="margin-bottom:1.5rem">Corporation Projects</div>
      <div id="projects-grid" class="layout"></div>
    </div>

    <!-- Member view -->
    <div id="view-member" class="hidden">
      <div id="member-error" class="banner"></div>
      <div class="page-title" style="margin-bottom:1.5rem">Member Profile</div>
      <div id="member-grid" class="layout"></div>
    </div>
  </main>

</div>

<script>
  let currentUser = null;
  let projectsTimer = null;

  // ── Boot ──────────────────────────────────────────────────────────────────

  async function boot() {
    try {
      const r = await fetch('/api/auth/me');
      if (r.status === 401) {
        show('view-login');
        return;
      }
      currentUser = await r.json();
      const portrait = document.getElementById('nav-portrait');
      portrait.src = `https://images.evetech.net/characters/${currentUser.character_id}/portrait?size=64`;
      document.getElementById('nav-name').textContent = currentUser.character_name;
      show('view-app');
      showView('projects');
    } catch {
      show('view-login');
    }
  }

  // ── View switching ────────────────────────────────────────────────────────

  function show(id) {
    ['view-loading','view-login','view-app'].forEach(v =>
      document.getElementById(v).classList.toggle('hidden', v !== id)
    );
  }

  function showView(view) {
    document.getElementById('view-projects').classList.toggle('hidden', view !== 'projects');
    document.getElementById('view-member').classList.toggle('hidden', view !== 'member');
    document.getElementById('btn-projects').classList.toggle('active', view === 'projects');
    document.getElementById('btn-member').classList.toggle('active', view === 'member');

    if (view === 'projects') loadProjects();
    if (view === 'member') loadMember();
  }

  // ── Projects ──────────────────────────────────────────────────────────────

  async function loadProjects() {
    if (projectsTimer) clearTimeout(projectsTimer);
    try {
      const r = await fetch('/api/projects');
      if (!r.ok) {
        showError('projects-error', r.status === 403
          ? 'Insufficient corporation roles to view projects.'
          : 'ESI unavailable — projects could not be loaded.');
        return;
      }
      hideError('projects-error');
      const projects = await r.json();
      renderProjects(projects);
    } catch {
      showError('projects-error', 'Network error — could not load projects.');
    }
    projectsTimer = setTimeout(loadProjects, 60000);
  }

  function renderProjects(projects) {
    const grid = document.getElementById('projects-grid');
    if (!projects.length) {
      grid.innerHTML = '<p style="color:#475569;font-size:0.85rem">No active projects.</p>';
      return;
    }

    grid.innerHTML = projects.map(p => {
      const items = p.required_deliverables || [];
      const pct = items.length
        ? Math.round(items.reduce((s, i) => s + (i.delivered_quantity / (i.required_quantity || 1)), 0) / items.length * 100)
        : 0;

      const typeLabel = { 'ItemDelivery': 'Item Delivery', 'Manual': 'Manual', 'Buyback': 'Buyback' }[p.type] || p.type || 'Project';
      const badgeClass = p.type === 'Buyback' ? 'badge-orange' : p.type === 'Manual' ? 'badge-muted' : 'badge-blue';

      const isBuyback = p.type === 'Buyback' && items.length;
      const tableHTML = isBuyback ? `
        <table>
          <thead>
            <tr>
              <th>Item</th>
              <th>Required</th>
              <th>Progress</th>
              <th>Corp Buy Price</th>
            </tr>
          </thead>
          <tbody>
            ${items.map(i => `
              <tr>
                <td>${i.type_name || i.type_id}</td>
                <td>${fmtNum(i.required_quantity)}</td>
                <td>${Math.round((i.delivered_quantity / (i.required_quantity || 1)) * 100)}%</td>
                <td>${i.corp_buy_price != null ? fmtISK(i.corp_buy_price) + ' ISK' : '<span style="color:#475569">–</span>'}</td>
              </tr>`).join('')}
          </tbody>
        </table>` : '';

      return `
        <div class="card">
          <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:0.75rem">
            <span style="font-weight:600;color:#f1f5f9">${escHtml(p.name || 'Unnamed Project')}</span>
            <span class="badge ${badgeClass}">${typeLabel}</span>
          </div>
          <div class="progress-wrap">
            <div class="progress-label">
              <span>Progress</span>
              <span>${pct}%</span>
            </div>
            <div class="progress-bar"><div class="progress-fill" style="width:${pct}%"></div></div>
          </div>
          ${tableHTML}
        </div>`;
    }).join('');
  }

  // ── Member ────────────────────────────────────────────────────────────────

  async function loadMember() {
    try {
      const r = await fetch('/api/member');
      if (!r.ok) {
        showError('member-error', 'ESI unavailable — member data could not be loaded.');
        return;
      }
      hideError('member-error');
      renderMember(await r.json());
    } catch {
      showError('member-error', 'Network error — could not load member data.');
    }
  }

  function renderMember(m) {
    const secColor = m.security_status >= 0 ? '#4ade80' : '#f87171';
    const secSign = m.security_status >= 0 ? '+' : '';
    const trainingBadge = m.training_active
      ? '<span class="badge badge-green">Training</span>'
      : '<span class="badge badge-muted">Idle</span>';

    document.getElementById('member-grid').innerHTML = `
      <div class="card">
        <div class="card-title">Character</div>
        <div class="char-card">
          <img class="char-portrait" src="https://images.evetech.net/characters/${m.character_id}/portrait?size=128" alt="">
          <div class="char-info">
            <div class="name">${escHtml(m.character_name)}</div>
            <div class="corp" id="corp-name-display">Corp ID: ${m.corporation_id}</div>
            <div class="sec-status" style="color:${secColor}">
              Security Status: ${secSign}${m.security_status.toFixed(1)}
            </div>
          </div>
        </div>
      </div>
      <div class="card">
        <div class="card-title">Financials &amp; Skills</div>
        <div class="metrics">
          <div class="metric">
            <div class="label">Wallet Balance</div>
            <div class="val">${fmtISK(m.wallet_balance)}<span class="unit">ISK</span></div>
          </div>
          <div class="metric">
            <div class="label">Total Skillpoints</div>
            <div class="val">${fmtSP(m.total_sp)}<span class="unit">SP</span></div>
          </div>
        </div>
        <div style="margin-top:0.75rem;font-size:0.75rem;color:#64748b;text-transform:uppercase;letter-spacing:0.06em">
          Skill Queue &nbsp; ${trainingBadge}
        </div>
      </div>`;

    // Resolve corp name from ESI public data
    fetch(`https://esi.evetech.net/latest/corporations/${m.corporation_id}/`)
      .then(r => r.json())
      .then(d => {
        const el = document.getElementById('corp-name-display');
        if (el && d.name) el.textContent = d.name;
      }).catch(() => {});
  }

  // ── Helpers ───────────────────────────────────────────────────────────────

  function fmtISK(n) {
    if (n >= 1e12) return (n / 1e12).toFixed(2) + 'T';
    if (n >= 1e9)  return (n / 1e9).toFixed(2) + 'B';
    if (n >= 1e6)  return (n / 1e6).toFixed(2) + 'M';
    return new Intl.NumberFormat().format(Math.round(n));
  }

  function fmtSP(n) {
    if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
    if (n >= 1e3) return (n / 1e3).toFixed(0) + 'K';
    return String(n);
  }

  function fmtNum(n) { return new Intl.NumberFormat().format(n); }

  function escHtml(s) {
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }

  function showError(id, msg) {
    const el = document.getElementById(id);
    el.textContent = msg;
    el.classList.add('show');
  }

  function hideError(id) {
    document.getElementById(id).classList.remove('show');
  }

  boot();
</script>
</body>
</html>
```

- [ ] **Step 2: Commit**

```bash
git add static/index.html
git commit -m "feat: complete frontend SPA — login, projects dashboard, member profile"
```

---

### Task 8: Docker Setup

**Files:**
- Create: `docker-compose.yml`

- [ ] **Step 1: Create `docker-compose.yml`**

```yaml
services:
  app:
    image: tiangolo/uvicorn-gunicorn-fastapi:python3.11
    ports:
      - "8760:80"
    volumes:
      - /srv/dev-disk-by-uuid-38b0ee7f-c1e1-4567-96bd-305378001aeb/nasty2/html/eve-chuji-homepage:/app
      - /srv/dev-disk-by-uuid-38b0ee7f-c1e1-4567-96bd-305378001aeb/nasty2/html/eve-chuji-homepage/data:/app/data
    env_file:
      - /srv/dev-disk-by-uuid-38b0ee7f-c1e1-4567-96bd-305378001aeb/nasty2/html/eve-chuji-homepage/.env
    environment:
      - MODULE_NAME=main
      - WEB_CONCURRENCY=1
    restart: unless-stopped
```

> `WEB_CONCURRENCY=1` is required for SQLite — aiosqlite does not support concurrent gunicorn workers safely. `MODULE_NAME=main` tells the image to load `main:app` instead of the default `app.main:app`.

- [ ] **Step 2: Verify `prestart.sh` will be found by the image**

The `tiangolo/uvicorn-gunicorn-fastapi` image automatically runs `/app/prestart.sh` before starting the server if the file exists. Since `prestart.sh` is in the repo root and the repo root is mounted to `/app`, no additional config is needed.

- [ ] **Step 3: Run full test suite one final time**

```bash
pytest -v
```
Expected: all tests PASS

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml
git commit -m "feat: docker-compose with NAS volume mount and tiangolo FastAPI image"
```

---

## Post-Implementation Verification Checklist

- [ ] Generate `SECRET_KEY`: `openssl rand -hex 32` and add to `.env`
- [ ] Deploy files to NAS: `rsync -av --exclude='.git' --exclude='__pycache__' . paolo@nasty:/srv/dev-disk-by-uuid-38b0ee7f-c1e1-4567-96bd-305378001aeb/nasty2/html/eve-chuji-homepage/`
- [ ] Start container via Proxteiner API or `docker compose up -d`
- [ ] Open https://chuji.swoojeff.online — login view should appear
- [ ] Login with EVE SSO — should redirect back and show Projects view
- [ ] Verify non-corp member gets 403 error page

---

## Known Risks / Verification Points

| Area | Risk | How to verify |
|------|------|---------------|
| ESI corp projects | Field names (`required_deliverables`, `type`, `status`) may differ from spec assumptions | Check raw ESI response after first login; adjust field names in `main.py:projects()` |
| Janice API | Response JSON schema in `janice.py:_fetch_prices()` is assumed (`items[].prices.buy.fivePercent`). QcoH7M (https://janice.e-351.com/a/QcoH7M) is a manual reference for gas project prices — not used by the API. | POST a single known item name to Janice and inspect the raw JSON response; adjust field paths in `_fetch_prices()` |
| Skill queue detection | Current logic in `main.py:member()` is approximate | Verify against `/characters/{id}/skillqueue/` endpoint if needed |
