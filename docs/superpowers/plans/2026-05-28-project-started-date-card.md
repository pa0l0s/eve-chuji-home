# Project Started Date on Card Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show each project's `details.created` date on its card, on the same line as "Last modified" (Started on the left, Last modified on the right).

**Architecture:** ESI's listing endpoint omits `created`; the detail endpoint provides it under `details.created`. Cache the immutable per-project value in a new `project_created_cache` SQLite table so the detail endpoint is only hit once per project lifetime. `/api/projects` enriches each project with `p["created"]` from cache (filling the cache lazily for projects it hasn't seen yet). Frontend swaps the existing single-line caption for a flex row with both dates.

**Tech Stack:** FastAPI + `aiosqlite` (Python), plain HTML/CSS/JS in `static/index.html`, `pytest` + `respx`.

---

## Preconditions

- Branch: work on `master`; commit there.
- Working tree starts clean.
- Spec: `docs/superpowers/specs/2026-05-28-project-started-date-card-design.md`.
- `_safe` is defined at `main.py:1151` (returns `(result, error_string)`, never raises). `asyncio` is already imported.

## File Structure

- **Modify** `db.py` — add `project_created_cache` CREATE TABLE in `init_db()`, plus `get_cached_project_created_map()` and `cache_project_created_batch()` accessors.
- **Modify** `esi.py` — add `get_corp_project_detail()` next to `get_corp_projects()`.
- **Modify** `main.py` — extend `/api/projects` to (a) load the created-cache, (b) fetch detail for any missing project IDs, (c) write new rows back to the cache, (d) attach `p["created"]` to every project.
- **Modify** `static/index.html` — swap the `Last modified:` div for a flex row with `Started` on the left and `Last modified` on the right.
- **Create** `tests/test_project_created.py` — DB accessor tests + ESI fetch test + `/api/projects` enrichment tests (first-load, cache-hit, detail-failure).

---

## Task 1: `project_created_cache` table + DB accessors

**Files:**
- Modify: `db.py` (new CREATE TABLE near line 89, new accessors near line 305)
- Test: `tests/test_project_created.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_project_created.py` with exactly this content:

```python
import pytest

from db import (
    init_db,
    get_cached_project_created_map,
    cache_project_created_batch,
)


@pytest.fixture(autouse=True)
async def _db():
    await init_db()


async def test_init_db_is_idempotent_for_created_cache():
    await init_db()  # second run must not raise


async def test_empty_cache_returns_empty_map():
    assert await get_cached_project_created_map() == {}


async def test_cache_batch_round_trip():
    await cache_project_created_batch([
        ("uuid-a", "2025-06-01T00:00:00Z"),
        ("uuid-b", "2025-07-15T12:30:00Z"),
    ])
    assert await get_cached_project_created_map() == {
        "uuid-a": "2025-06-01T00:00:00Z",
        "uuid-b": "2025-07-15T12:30:00Z",
    }


async def test_cache_batch_upsert_overwrites():
    await cache_project_created_batch([("uuid-a", "2025-06-01T00:00:00Z")])
    await cache_project_created_batch([("uuid-a", "2099-01-01T00:00:00Z")])
    assert (await get_cached_project_created_map())["uuid-a"] == "2099-01-01T00:00:00Z"


async def test_cache_batch_empty_is_noop():
    await cache_project_created_batch([])
    assert await get_cached_project_created_map() == {}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_project_created.py -v`
Expected: FAIL — `ImportError: cannot import name 'get_cached_project_created_map' from 'db'`.

- [ ] **Step 3: Add the `project_created_cache` CREATE TABLE**

In `db.py`, find the `ship_types_cache` CREATE TABLE (near line 89). Immediately **after** that block (before the `# Migrations:` comment near line 96), insert:

```python
        await db.execute("""
            CREATE TABLE IF NOT EXISTS project_created_cache (
                project_id TEXT PRIMARY KEY,
                created    TEXT NOT NULL,
                cached_at  REAL NOT NULL
            )
        """)
```

- [ ] **Step 4: Add the accessors**

In `db.py`, at the end of the file, append:

```python


async def get_cached_project_created_map() -> dict[str, str]:
    async with aiosqlite.connect(_db_path()) as db:
        async with db.execute(
            "SELECT project_id, created FROM project_created_cache"
        ) as cur:
            return {r[0]: r[1] for r in await cur.fetchall()}


async def cache_project_created_batch(items: list[tuple[str, str]]) -> None:
    if not items:
        return
    now = time.time()
    async with aiosqlite.connect(_db_path()) as db:
        await db.executemany(
            """INSERT INTO project_created_cache (project_id, created, cached_at)
               VALUES (?, ?, ?)
               ON CONFLICT(project_id) DO UPDATE SET
                 created   = excluded.created,
                 cached_at = excluded.cached_at""",
            [(pid, c, now) for (pid, c) in items],
        )
        await db.commit()
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_project_created.py -v`
Expected: PASS — 5 passed.

- [ ] **Step 6: Commit**

```bash
git add db.py tests/test_project_created.py
git commit -m "$(cat <<'EOF'
feat: add project_created_cache table + accessors

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: ESI `get_corp_project_detail` helper

**Files:**
- Modify: `esi.py` (new function next to `get_corp_projects` near line 307)
- Test: `tests/test_project_created.py` (append)

- [ ] **Step 1: Write the failing test**

Append exactly this to `tests/test_project_created.py`:

```python
import respx
from httpx import Response

from esi import get_corp_project_detail

CORP = 98340844  # conftest sets CORP_ID=98340844
PROJECT_URL = f"https://esi.evetech.net/corporations/{CORP}/projects/p1"


@respx.mock
async def test_get_corp_project_detail_returns_json():
    respx.get(PROJECT_URL).mock(return_value=Response(200, json={
        "id": "p1",
        "name": "P1",
        "state": "Active",
        "last_modified": "2025-08-26T00:00:00Z",
        "details": {"created": "2025-06-01T00:00:00Z"},
        "progress": {"current": 5, "desired": 10},
    }))
    detail = await get_corp_project_detail(CORP, "p1", "tok")
    assert detail["details"]["created"] == "2025-06-01T00:00:00Z"


@respx.mock
async def test_get_corp_project_detail_raises_on_http_error():
    respx.get(PROJECT_URL).mock(return_value=Response(500, json={"error": "x"}))
    with pytest.raises(Exception):
        await get_corp_project_detail(CORP, "p1", "tok")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_project_created.py -v`
Expected: 2 new tests FAIL — `ImportError: cannot import name 'get_corp_project_detail' from 'esi'`.

- [ ] **Step 3: Add `get_corp_project_detail` to `esi.py`**

In `esi.py`, find `get_corp_projects` (near line 307). Immediately **after** that function (before `resolve_names` near line 333), insert:

```python


async def get_corp_project_detail(corporation_id: int, project_id: str, access_token: str) -> dict:
    # New ESI endpoint; same X-Compatibility-Date contract as get_corp_projects.
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"https://esi.evetech.net/corporations/{corporation_id}/projects/{project_id}",
            headers={
                "Authorization": f"Bearer {access_token}",
                "X-Compatibility-Date": "2026-01-01",
            },
        )
        r.raise_for_status()
        return r.json()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_project_created.py -v`
Expected: PASS — 7 passed total (5 from Task 1 + 2 new).

- [ ] **Step 5: Commit**

```bash
git add esi.py tests/test_project_created.py
git commit -m "$(cat <<'EOF'
feat: add ESI get_corp_project_detail helper

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: `/api/projects` enrichment with cached `created`

**Files:**
- Modify: `main.py` (the import block at the top; the priority-assignment block near line 700)
- Test: `tests/test_project_created.py` (append)

- [ ] **Step 1: Write the failing tests**

Append exactly this to `tests/test_project_created.py`:

```python
import time

from db import upsert_token, get_cached_project_created_map
from auth import make_session_cookie


async def _user_cookie(character_id: int = 777) -> dict:
    await upsert_token(
        character_id=character_id, character_name="Pilot",
        access_token="acc", refresh_token="ref",
        expires_at=time.time() + 1200, corporation_id=98340844,
    )
    return {"session": make_session_cookie(character_id)}


PROJECTS_URL = f"https://esi.evetech.net/corporations/{CORP}/projects"
IDS_URL = "https://esi.evetech.net/latest/universe/ids/"


def _detail_url(pid: str) -> str:
    return f"https://esi.evetech.net/corporations/{CORP}/projects/{pid}"


@respx.mock
async def test_projects_first_call_fetches_and_caches_created(client):
    respx.get(PROJECTS_URL).mock(return_value=Response(200, json={"projects": [
        {"id": "p1", "name": "P1", "state": "Active",
         "last_modified": "2025-08-26T00:00:00Z",
         "progress": {"current": 5, "desired": 10}},
    ]}))
    respx.post(IDS_URL).mock(return_value=Response(200, json={}))
    respx.get(_detail_url("p1")).mock(return_value=Response(200, json={
        "id": "p1", "name": "P1", "state": "Active",
        "last_modified": "2025-08-26T00:00:00Z",
        "details": {"created": "2025-06-01T00:00:00Z"},
        "progress": {"current": 5, "desired": 10},
    }))
    cookies = await _user_cookie()
    r = await client.get("/api/projects", cookies=cookies)
    assert r.status_code == 200
    proj = next(p for p in r.json()["active"] if p["id"] == "p1")
    assert proj["created"] == "2025-06-01T00:00:00Z"
    # And it's persisted in the cache for next time.
    assert (await get_cached_project_created_map())["p1"] == "2025-06-01T00:00:00Z"


@respx.mock
async def test_projects_second_call_serves_created_from_cache(client):
    # Prime the cache; deliberately omit the detail mock — if the endpoint
    # tries to fetch detail, respx.mock will raise.
    await cache_project_created_batch([("p1", "2025-06-01T00:00:00Z")])
    respx.get(PROJECTS_URL).mock(return_value=Response(200, json={"projects": [
        {"id": "p1", "name": "P1", "state": "Active",
         "last_modified": "2025-08-26T00:00:00Z",
         "progress": {"current": 5, "desired": 10}},
    ]}))
    respx.post(IDS_URL).mock(return_value=Response(200, json={}))
    cookies = await _user_cookie()
    r = await client.get("/api/projects", cookies=cookies)
    assert r.status_code == 200
    proj = next(p for p in r.json()["active"] if p["id"] == "p1")
    assert proj["created"] == "2025-06-01T00:00:00Z"


@respx.mock
async def test_projects_detail_failure_omits_created(client):
    respx.get(PROJECTS_URL).mock(return_value=Response(200, json={"projects": [
        {"id": "p1", "name": "P1", "state": "Active",
         "last_modified": "2025-08-26T00:00:00Z",
         "progress": {"current": 5, "desired": 10}},
    ]}))
    respx.post(IDS_URL).mock(return_value=Response(200, json={}))
    respx.get(_detail_url("p1")).mock(return_value=Response(502, json={"error": "boom"}))
    cookies = await _user_cookie()
    r = await client.get("/api/projects", cookies=cookies)
    assert r.status_code == 200
    proj = next(p for p in r.json()["active"] if p["id"] == "p1")
    assert "created" not in proj
    # No row written to the cache when the detail fetch fails.
    assert "p1" not in await get_cached_project_created_map()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_project_created.py -v`
Expected: the 3 new tests FAIL — `proj["created"]` doesn't exist because `/api/projects` doesn't attach it yet.

- [ ] **Step 3: Import the new ESI helper in `main.py`**

In `main.py`, find the line that imports `get_corp_projects` from `esi` (line 27). It currently reads:

```python
    get_corp_projects, get_corp_project_contributors,
```

Change it to:

```python
    get_corp_projects, get_corp_project_detail, get_corp_project_contributors,
```

- [ ] **Step 4: Enrich `/api/projects` with `created`**

In `main.py`, find the priority-assignment block near line 700–705. It currently reads:

```python
    excluded_names = await database.list_excluded_projects()
    special_names = await database.list_special_projects()
    tracked_names = await database.list_tracked_projects()
    for p in active + closed:
        _compute_project_warn(p, excluded_names)
        p["sort_priority"] = _project_priority(p, tracked_names)
        p["special"] = (p.get("name") or "") in special_names
```

Immediately **after** that block (before the `# Special projects show their top 3 contributors` comment), insert:

```python
    # Started-date enrichment: pull `created` from a per-project cache, lazily
    # filling the cache from the ESI detail endpoint for any project we haven't
    # seen yet. `created` is immutable per project, so once cached we never
    # refetch. Best-effort — a failed detail fetch just leaves p["created"] off.
    created_map = await database.get_cached_project_created_map()
    missing = [
        p["id"] for p in active + closed
        if p.get("id") and p["id"] not in created_map
    ]
    if missing:
        details = await asyncio.gather(*[
            _safe(get_corp_project_detail(CORP_ID, pid, access_token))
            for pid in missing
        ])
        new_rows: list[tuple[str, str]] = []
        for pid, (detail, _err) in zip(missing, details):
            if detail:
                c = (detail.get("details") or {}).get("created")
                if c:
                    created_map[pid] = c
                    new_rows.append((pid, c))
        if new_rows:
            await database.cache_project_created_batch(new_rows)
    for p in active + closed:
        pid = p.get("id")
        if pid and pid in created_map:
            p["created"] = created_map[pid]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_project_created.py -v`
Expected: PASS — 10 passed total.

Run: `pytest -q`
Expected: PASS — full suite green (no regression in `test_track_projects.py`, `test_special_projects.py`, etc.).

- [ ] **Step 6: Commit**

```bash
git add main.py tests/test_project_created.py
git commit -m "$(cat <<'EOF'
feat: enrich /api/projects with cached `created` from ESI detail endpoint

Lazily fills project_created_cache on first sighting of each project; all
subsequent /api/projects calls serve `created` from cache (zero extra ESI
fetches). Detail-fetch failure leaves the project without `created` and is
retried on the next call.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Frontend — Started + Last modified flex row

**Files:**
- Modify: `static/index.html` (the project-card date caption near line 1983–1985)

No automated frontend tests — verify via static checks.

- [ ] **Step 1: Replace the date caption with a flex row**

In `static/index.html`, find the date caption inside `projectCard` (around line 1983–1985). It currently reads:

```html
        <div style="margin-top:0.75rem;font-size:0.7rem;color:#475569">
          Last modified: ${new Date(p.last_modified).toLocaleDateString()}
        </div>
```

Change it to:

```html
        <div style="margin-top:0.75rem;font-size:0.7rem;color:#475569;display:flex;justify-content:space-between;gap:0.5rem">
          <span>${p.created ? 'Started: ' + new Date(p.created).toLocaleDateString() : ''}</span>
          <span>Last modified: ${new Date(p.last_modified).toLocaleDateString()}</span>
        </div>
```

- [ ] **Step 2: Static verification**

Confirm via grep:
- `grep -n "Started:" static/index.html` returns the new line.
- `grep -n "justify-content:space-between" static/index.html` returns at least the new card row.
- `grep -nc "p.last_modified" static/index.html` shows the existing reference is preserved.

- [ ] **Step 3: Run the test suite once more**

Run: `pytest -q`
Expected: PASS — no Python changes, but ensures the suite is still green before commit.

- [ ] **Step 4: Manual UI verification**

Start the app per the "Running the app locally" memory and open the Projects page in a browser:

```bash
set -a && source .env && set +a && ~/.local/bin/uvicorn main:app --port 8000
```

Expectations:
1. First load after deploy: cards may briefly show only "Last modified" on the right while the cache fills (the cache fills on the request that returns the listing, so by the time the response reaches the browser `created` should already be populated for the projects whose detail fetch succeeded).
2. After cache fill: every card shows `Started: <date>` on the left and `Last modified: <date>` on the right of the same line.
3. A project whose detail fetch failed shows only "Last modified" on the right; left is empty, layout doesn't shift.

- [ ] **Step 5: Commit**

```bash
git add static/index.html
git commit -m "$(cat <<'EOF'
feat: show project Started date on card alongside Last modified

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Done

Four independent commits. After Task 4:

- `git log --oneline -5` shows the four feature commits plus the earlier spec commit.
- `pytest -q` passes.
- Project cards show `Started: <date>` on the left and `Last modified: <date>` on the right.
- The cache fills lazily on first request; subsequent loads add zero extra ESI calls.
