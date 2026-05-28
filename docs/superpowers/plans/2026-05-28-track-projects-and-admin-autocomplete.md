# Track Projects + Admin Name Autocomplete Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the hard-coded `PROJECT_TRACKING_NAMES` set into a DB-backed `track` flag on `project_settings`, toggleable from the admin panel like `special` and `price_excluded`. Add a native `<datalist>` autocomplete on the admin "Add project" input, sourced from the corporation's actual ESI projects.

**Architecture:** New `track` column on `project_settings` with a migration that seeds the two legacy names once. `/api/projects` reads tracked names from the DB instead of the in-code constant; the existing `sort_priority` + purple "Track" badge plumbing is untouched. Admin UI gains a third checkbox column and a `<datalist>` populated from a new admin-only `GET /api/admin/projects/available` endpoint that returns the corp's project names from ESI.

**Tech Stack:** FastAPI + `aiosqlite` (Python), plain HTML/CSS/JS in `static/index.html`, `pytest` + `respx`.

---

## Preconditions

- Branch: work on `master`; commit there.
- `respx` / `pytest` are available — `pytest` is the standard test runner.
- The spec is `docs/superpowers/specs/2026-05-28-track-projects-and-admin-autocomplete-design.md`.
- Working tree is clean.

## File Structure

- **Modify** `db.py` — add `track` column to `project_settings` CREATE TABLE; migration block that adds the column and one-time-seeds the two legacy names; new `list_tracked_projects` + `set_project_track`; extended `list_project_settings` SELECT.
- **Modify** `main.py` — admin POST handles `track`; delete `PROJECT_TRACKING_NAMES`; `_project_priority(p, tracked_names)`; `/api/projects` loads tracked names; new `GET /api/admin/projects/available` admin endpoint.
- **Modify** `static/index.html` — admin "Track" column + count badge + `adminToggleProjectTrack`; `<datalist>` autocomplete wired to the "Add project" input; `loadAdminProjects` fetches available names.
- **Create** `tests/test_track_projects.py` — DB + endpoint tests for track flag, migration seed, and the available-projects endpoint.

---

## Task 1: `track` column + DB accessors + migration seed

**Files:**
- Modify: `db.py` (the `project_settings` CREATE TABLE near line 81; the existing migration block near line 95–100; accessors near line 288–305)
- Test: `tests/test_track_projects.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_track_projects.py` with exactly this content:

```python
import pytest

from db import (
    init_db, set_project_track, list_tracked_projects,
    list_project_settings, set_project_special, set_project_price_excluded,
)


@pytest.fixture(autouse=True)
async def _db():
    await init_db()


async def test_init_db_is_idempotent():
    await init_db()  # second run must not raise (track column already present)


async def test_set_and_list_tracked_projects():
    await set_project_track("Armor Remote Repair", True)
    await set_project_track("Remote Boost Shield", True)
    await set_project_track("Ordinary Project", False)
    assert await list_tracked_projects() == {"Armor Remote Repair", "Remote Boost Shield"}


async def test_track_is_independent_of_special_and_excluded():
    await set_project_track("Armor Remote Repair", True)
    await set_project_special("Armor Remote Repair", True)
    await set_project_price_excluded("Armor Remote Repair", False)
    settings = {s["project_name"]: s for s in await list_project_settings()}
    row = settings["Armor Remote Repair"]
    assert row["track"] == 1
    assert row["special"] == 1
    assert row["price_excluded"] == 0


async def test_fresh_db_seeds_legacy_track_names():
    # init_db ran in the autouse fixture — fresh DB should have the two legacy
    # names seeded with track=1.
    assert await list_tracked_projects() == {"Remote Boost Shield", "Armor Remote Repair"}


async def test_seed_does_not_overwrite_after_admin_disable():
    # Admin turns one off, then init_db runs again (next app boot). Flag must
    # stay off — the seed only runs when the `track` column is added.
    await set_project_track("Armor Remote Repair", False)
    await init_db()
    assert await list_tracked_projects() == {"Remote Boost Shield"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_track_projects.py -v`
Expected: FAIL — `ImportError: cannot import name 'set_project_track' from 'db'`.

- [ ] **Step 3: Add `track` to the `project_settings` CREATE TABLE**

In `db.py`, change the `project_settings` CREATE TABLE (near line 80) from:

```python
        await db.execute("""
            CREATE TABLE IF NOT EXISTS project_settings (
                project_name   TEXT PRIMARY KEY,
                price_excluded INTEGER DEFAULT 0,
                special        INTEGER DEFAULT 0,
                updated_at     REAL NOT NULL
            )
        """)
```

to:

```python
        await db.execute("""
            CREATE TABLE IF NOT EXISTS project_settings (
                project_name   TEXT PRIMARY KEY,
                price_excluded INTEGER DEFAULT 0,
                special        INTEGER DEFAULT 0,
                track          INTEGER DEFAULT 0,
                updated_at     REAL NOT NULL
            )
        """)
```

- [ ] **Step 4: Add the migration + seed for pre-existing DBs**

In `db.py`, find the existing `special`-column migration block (lines 95–100). It currently reads:

```python
        # Migration: add `special` to project_settings on pre-existing DBs.
        async with db.execute("PRAGMA table_info(project_settings)") as cur:
            _cols = {r[1] for r in await cur.fetchall()}
        if "special" not in _cols:
            await db.execute(
                "ALTER TABLE project_settings ADD COLUMN special INTEGER DEFAULT 0")
```

Replace that entire block with:

```python
        # Migrations: add new columns on pre-existing DBs.
        async with db.execute("PRAGMA table_info(project_settings)") as cur:
            _cols = {r[1] for r in await cur.fetchall()}
        if "special" not in _cols:
            await db.execute(
                "ALTER TABLE project_settings ADD COLUMN special INTEGER DEFAULT 0")
        if "track" not in _cols:
            await db.execute(
                "ALTER TABLE project_settings ADD COLUMN track INTEGER DEFAULT 0")
            # Seed the two legacy hard-coded Track names on pre-existing DBs.
            # Runs only when the column was just added, so an admin who later
            # disables Track on one of these names won't have it re-enabled
            # on the next boot.
            _seed_now = time.time()
            for _name in ("Remote Boost Shield", "Armor Remote Repair"):
                await db.execute(
                    """INSERT INTO project_settings (project_name, track, updated_at)
                       VALUES (?, 1, ?)
                       ON CONFLICT(project_name) DO UPDATE SET
                         track      = 1,
                         updated_at = excluded.updated_at""",
                    (_name, _seed_now))
```

- [ ] **Step 5: Seed the legacy Track names on fresh DBs**

A fresh DB takes a different path: the `track` column comes from the new
`CREATE TABLE`, so the migration block from Step 4 doesn't fire. The legacy
names must be added by the existing empty-table seed.

In `db.py`, find the empty-table seed block (lines 102–119). It currently reads:

```python
        # One-time seed of NPC-priced project names — only when table is empty,
        # so admin edits stick across restarts.
        async with db.execute("SELECT COUNT(*) FROM project_settings") as cur:
            row = await cur.fetchone()
            if row and row[0] == 0:
                now = time.time()
                defaults = (
                    "Neural Network Analyzer",
                    "Ancient Coordinates Database",
                    "Sleeper Data Library",
                    "Sleeper Drone AI Nexus",
                    "Triglavian Survey Database",
                    "Compressed Gneiss",
                )
                await db.executemany(
                    "INSERT INTO project_settings (project_name, price_excluded, updated_at) VALUES (?, 1, ?)",
                    [(n, now) for n in defaults],
                )
```

Replace that entire block with:

```python
        # One-time seed — only when table is empty, so admin edits stick.
        # Two seed groups:
        #   - NPC-priced names with price_excluded=1
        #   - Legacy hard-coded Track names with track=1 (mirrors the
        #     pre-existing-DB migration in Step 4 above).
        async with db.execute("SELECT COUNT(*) FROM project_settings") as cur:
            row = await cur.fetchone()
            if row and row[0] == 0:
                now = time.time()
                defaults = (
                    "Neural Network Analyzer",
                    "Ancient Coordinates Database",
                    "Sleeper Data Library",
                    "Sleeper Drone AI Nexus",
                    "Triglavian Survey Database",
                    "Compressed Gneiss",
                )
                await db.executemany(
                    "INSERT INTO project_settings (project_name, price_excluded, updated_at) VALUES (?, 1, ?)",
                    [(n, now) for n in defaults],
                )
                await db.executemany(
                    "INSERT INTO project_settings (project_name, track, updated_at) VALUES (?, 1, ?)",
                    [(n, now) for n in ("Remote Boost Shield", "Armor Remote Repair")],
                )
```

- [ ] **Step 6: Add the accessors**

In `db.py`, immediately after the `set_project_special` function (it ends near line 305 with `await db.commit()`), add:

```python


async def list_tracked_projects() -> set[str]:
    async with aiosqlite.connect(_db_path()) as db:
        async with db.execute(
            "SELECT project_name FROM project_settings WHERE track = 1"
        ) as cur:
            return {r[0] for r in await cur.fetchall()}


async def set_project_track(name: str, track: bool):
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            """INSERT INTO project_settings (project_name, track, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(project_name) DO UPDATE SET
                 track      = excluded.track,
                 updated_at = excluded.updated_at""",
            (name, 1 if track else 0, time.time()))
        await db.commit()
```

- [ ] **Step 7: Extend `list_project_settings` to include `track`**

In `db.py`, in `list_project_settings`, change the SELECT from:

```python
            "SELECT project_name, price_excluded, special, updated_at "
            "FROM project_settings ORDER BY project_name"
```

to:

```python
            "SELECT project_name, price_excluded, special, track, updated_at "
            "FROM project_settings ORDER BY project_name"
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `pytest tests/test_track_projects.py -v`
Expected: PASS — 5 passed.

- [ ] **Step 9: Commit**

```bash
git add db.py tests/test_track_projects.py
git commit -m "$(cat <<'EOF'
feat: add `track` flag to project_settings

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Admin endpoint accepts `track`

**Files:**
- Modify: `main.py` (the `admin_set_project_setting` handler near line 444–453)
- Test: `tests/test_track_projects.py` (append)

- [ ] **Step 1: Write the failing tests**

Append exactly this to `tests/test_track_projects.py`:

```python
import time

from db import upsert_token
from auth import make_session_cookie


async def _admin_cookie(character_id: int = 777) -> dict:
    await upsert_token(
        character_id=character_id, character_name="Admin Pilot",
        access_token="acc", refresh_token="ref",
        expires_at=time.time() + 1200, corporation_id=98340844,
    )
    return {"session": make_session_cookie(character_id)}


async def test_admin_set_track_requires_admin(client):
    cookies = await _admin_cookie(555)  # not in ADMIN_CHARACTER_IDS
    r = await client.post("/api/admin/projects/settings",
                          json={"name": "My Project", "track": True},
                          cookies=cookies)
    assert r.status_code == 403


async def test_admin_set_track_flag(client, monkeypatch):
    monkeypatch.setenv("ADMIN_CHARACTER_IDS", "777")
    cookies = await _admin_cookie(777)
    r = await client.post("/api/admin/projects/settings",
                          json={"name": "My Project", "track": True},
                          cookies=cookies)
    assert r.status_code == 200
    assert "My Project" in await list_tracked_projects()


async def test_admin_track_independent_from_other_flags(client, monkeypatch):
    monkeypatch.setenv("ADMIN_CHARACTER_IDS", "777")
    cookies = await _admin_cookie(777)
    await client.post("/api/admin/projects/settings",
                      json={"name": "P1", "excluded": True}, cookies=cookies)
    await client.post("/api/admin/projects/settings",
                      json={"name": "P1", "special": True}, cookies=cookies)
    await client.post("/api/admin/projects/settings",
                      json={"name": "P1", "track": True}, cookies=cookies)
    settings = {s["project_name"]: s for s in await list_project_settings()}
    assert settings["P1"]["price_excluded"] == 1
    assert settings["P1"]["special"] == 1
    assert settings["P1"]["track"] == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_track_projects.py -v`
Expected: the 3 new tests FAIL — `test_admin_set_track_flag` and `test_admin_track_independent_from_other_flags` fail because the endpoint ignores `track` (the project never appears in `list_tracked_projects()`).

- [ ] **Step 3: Update the admin handler in `main.py`**

In `main.py`, find `admin_set_project_setting` (near line 444). It currently reads:

```python
@app.post("/api/admin/projects/settings")
async def admin_set_project_setting(body: dict, _admin: int = Depends(require_admin)):
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name required")
    if "excluded" in body:
        await database.set_project_price_excluded(name, bool(body["excluded"]))
    if "special" in body:
        await database.set_project_special(name, bool(body["special"]))
    return {"ok": True}
```

Change it to:

```python
@app.post("/api/admin/projects/settings")
async def admin_set_project_setting(body: dict, _admin: int = Depends(require_admin)):
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name required")
    if "excluded" in body:
        await database.set_project_price_excluded(name, bool(body["excluded"]))
    if "special" in body:
        await database.set_project_special(name, bool(body["special"]))
    if "track" in body:
        await database.set_project_track(name, bool(body["track"]))
    return {"ok": True}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_track_projects.py -v`
Expected: PASS — 8 passed (5 from Task 1 + 3 new).

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_track_projects.py
git commit -m "$(cat <<'EOF'
feat: admin projects endpoint accepts the \`track\` flag

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Replace `PROJECT_TRACKING_NAMES` with the DB lookup in `/api/projects`

**Files:**
- Modify: `main.py` (the `PROJECT_TRACKING_NAMES` constant at line 778; `_project_priority` near line 829; `/api/projects` priority assignment near line 678–683)
- Test: `tests/test_track_projects.py` (append)

- [ ] **Step 1: Write the failing test**

Append exactly this to `tests/test_track_projects.py`:

```python
import respx
from httpx import Response

CORP = "98340844"  # conftest sets CORP_ID=98340844
PROJECTS_URL = f"https://esi.evetech.net/corporations/{CORP}/projects"
IDS_URL = "https://esi.evetech.net/latest/universe/ids/"


@respx.mock
async def test_projects_sort_priority_uses_db_track_flag(client, monkeypatch):
    # Admin marks an unusual name (NOT one of the legacy hard-coded names) as
    # tracked. /api/projects must give it sort_priority=1.
    monkeypatch.setenv("ADMIN_CHARACTER_IDS", "777")
    cookies = await _admin_cookie(777)
    await client.post("/api/admin/projects/settings",
                      json={"name": "Custom Tracked Project", "track": True},
                      cookies=cookies)

    respx.get(PROJECTS_URL).mock(return_value=Response(200, json={"projects": [
        {"id": "p1", "name": "Custom Tracked Project", "state": "Active",
         "progress": {"current": 1, "desired": 10}},
        {"id": "p2", "name": "Unrelated Project", "state": "Active",
         "progress": {"current": 1, "desired": 10}},
    ]}))
    respx.post(IDS_URL).mock(return_value=Response(200, json={}))

    r = await client.get("/api/projects", cookies=cookies)
    assert r.status_code == 200
    actives = {p["name"]: p for p in r.json()["active"]}
    assert actives["Custom Tracked Project"]["sort_priority"] == 1
    assert actives["Unrelated Project"]["sort_priority"] == 5
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_track_projects.py::test_projects_sort_priority_uses_db_track_flag -v`
Expected: FAIL — `Custom Tracked Project` gets `sort_priority=5` because `_project_priority` still consults the in-code `PROJECT_TRACKING_NAMES` constant, which doesn't include it.

- [ ] **Step 3: Delete `PROJECT_TRACKING_NAMES`**

In `main.py`, find and delete the constant (around line 777–778). The current block reads:

```python
# Special-case priority buckets for the project list (lower number = higher priority).
PROJECT_TRACKING_NAMES = {"Remote Boost Shield", "Armor Remote Repair"}
PROJECT_LOOT_NAMES = {
    "Sleeper Data Library",
    "Ancient Coordinates Database",
    "Neural Network Analyzer",
    "Sleeper Drone AI Nexus",
    "Triglavian Survey Database",
}
```

Change it to:

```python
# Special-case priority buckets for the project list (lower number = higher priority).
# The Track set is now DB-backed (project_settings.track); see list_tracked_projects().
PROJECT_LOOT_NAMES = {
    "Sleeper Data Library",
    "Ancient Coordinates Database",
    "Neural Network Analyzer",
    "Sleeper Drone AI Nexus",
    "Triglavian Survey Database",
}
```

- [ ] **Step 4: Update `_project_priority` to take tracked names as a parameter**

In `main.py`, find `_project_priority` (near line 829). It currently reads:

```python
def _project_priority(p: dict) -> int:
    name = p.get("name") or ""
    warn = p.get("warn_level")
    if name in PROJECT_TRACKING_NAMES:
        return 1
    if warn == "red":
        return 2
    if warn == "yellow":
        return 3
    if name in PROJECT_LOOT_NAMES:
        return 4
    return 5
```

Change it to:

```python
def _project_priority(p: dict, tracked_names: set[str]) -> int:
    name = p.get("name") or ""
    warn = p.get("warn_level")
    if name in tracked_names:
        return 1
    if warn == "red":
        return 2
    if warn == "yellow":
        return 3
    if name in PROJECT_LOOT_NAMES:
        return 4
    return 5
```

- [ ] **Step 5: Load tracked names in `/api/projects` and pass them through**

In `main.py`, find the priority-assignment block near line 678. It currently reads:

```python
    excluded_names = await database.list_excluded_projects()
    special_names = await database.list_special_projects()
    for p in active + closed:
        _compute_project_warn(p, excluded_names)
        p["sort_priority"] = _project_priority(p)
        p["special"] = (p.get("name") or "") in special_names
```

Change it to:

```python
    excluded_names = await database.list_excluded_projects()
    special_names = await database.list_special_projects()
    tracked_names = await database.list_tracked_projects()
    for p in active + closed:
        _compute_project_warn(p, excluded_names)
        p["sort_priority"] = _project_priority(p, tracked_names)
        p["special"] = (p.get("name") or "") in special_names
```

- [ ] **Step 6: Run the new test + full file to verify everything passes**

Run: `pytest tests/test_track_projects.py -v`
Expected: PASS — 9 passed.

Run: `pytest tests/test_special_projects.py -v`
Expected: PASS — all existing tests still pass (the `_project_priority` signature change is the only callsite-affecting edit; the `special_names` plumbing is untouched).

- [ ] **Step 7: Commit**

```bash
git add main.py tests/test_track_projects.py
git commit -m "$(cat <<'EOF'
feat: drive Track priority from project_settings.track

Removes the hard-coded PROJECT_TRACKING_NAMES set; _project_priority now reads
the tracked names that /api/projects loads from the DB.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Admin "available project names" endpoint

**Files:**
- Modify: `main.py` (add a new route near the other admin project routes around line 439–459)
- Test: `tests/test_track_projects.py` (append)

- [ ] **Step 1: Write the failing tests**

Append exactly this to `tests/test_track_projects.py`:

```python
@respx.mock
async def test_admin_available_projects_returns_sorted_names(client, monkeypatch):
    monkeypatch.setenv("ADMIN_CHARACTER_IDS", "777")
    cookies = await _admin_cookie(777)
    respx.get(PROJECTS_URL).mock(return_value=Response(200, json={"projects": [
        {"id": "p1", "name": "Zeta Project", "state": "Active"},
        {"id": "p2", "name": "Alpha Project", "state": "Active"},
        {"id": "p3", "name": "Alpha Project", "state": "Closed"},  # dupe
        {"id": "p4", "name": "", "state": "Active"},                # empty
    ]}))
    r = await client.get("/api/admin/projects/available", cookies=cookies)
    assert r.status_code == 200
    body = r.json()
    assert body["names"] == ["Alpha Project", "Zeta Project"]


async def test_admin_available_projects_requires_admin(client):
    cookies = await _admin_cookie(555)
    r = await client.get("/api/admin/projects/available", cookies=cookies)
    assert r.status_code == 403


@respx.mock
async def test_admin_available_projects_handles_esi_failure(client, monkeypatch):
    monkeypatch.setenv("ADMIN_CHARACTER_IDS", "777")
    cookies = await _admin_cookie(777)
    respx.get(PROJECTS_URL).mock(return_value=Response(502, json={"error": "boom"}))
    r = await client.get("/api/admin/projects/available", cookies=cookies)
    assert r.status_code == 200
    body = r.json()
    assert body["names"] == []
    assert "error" in body
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_track_projects.py -v`
Expected: 3 new tests FAIL — `404 Not Found` (endpoint doesn't exist yet).

- [ ] **Step 3: Add the new endpoint in `main.py`**

In `main.py`, find the existing `admin_delete_project_setting` route (near line 456–459). Immediately **after** that route, insert this new route:

```python


@app.get("/api/admin/projects/available")
async def admin_available_projects(character_id: int = Depends(require_admin)):
    """Returns the corp's ESI project names for the admin autocomplete.
    Best-effort: any ESI failure returns 200 with an empty list and an error
    field, so the admin panel never breaks on transient network issues."""
    try:
        access_token = await get_valid_token(character_id)
        raw = await get_corp_projects(CORP_ID, access_token)
        names = sorted({p["name"] for p in raw if p.get("name")})
        return {"names": names}
    except Exception as e:
        return {"names": [], "error": str(e)}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_track_projects.py -v`
Expected: PASS — 12 passed.

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_track_projects.py
git commit -m "$(cat <<'EOF'
feat: GET /api/admin/projects/available for admin name autocomplete

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Admin UI — Track column, count badge, and toggle handler

**Files:**
- Modify: `static/index.html` (the `renderAdminProjects` function near line 2716–2764; new `adminToggleProjectTrack` near line 2786)

This task has no automated frontend tests — the project has no JS test harness. Verify manually.

- [ ] **Step 1: Extend `renderAdminProjects` — add the Track checkbox cell**

In `static/index.html`, find the `rows` mapping in `renderAdminProjects` (line 2718). It currently reads:

```javascript
    const rows = list.map(p => `
      <tr>
        <td style="color:#e2e8f0">${escHtml(p.project_name)}</td>
        <td style="text-align:center">
          <input type="checkbox" ${p.price_excluded ? 'checked' : ''}
                 onchange="adminToggleProjectExclusion('${escAttr(p.project_name)}', this.checked)">
        </td>
        <td style="text-align:center">
          <input type="checkbox" ${p.special ? 'checked' : ''}
                 onchange="adminToggleProjectSpecial('${escAttr(p.project_name)}', this.checked)">
        </td>
        <td>
          <button class="btn-logout" onclick="adminRemoveProjectSetting('${escAttr(p.project_name)}')">Remove</button>
        </td>
      </tr>`).join('') || `
      <tr><td colspan="4" style="color:#475569;text-align:center;padding:0.75rem 0;font-style:italic">
        No project settings. Add a project name below.
      </td></tr>`;
```

Change it to:

```javascript
    const rows = list.map(p => `
      <tr>
        <td style="color:#e2e8f0">${escHtml(p.project_name)}</td>
        <td style="text-align:center">
          <input type="checkbox" ${p.price_excluded ? 'checked' : ''}
                 onchange="adminToggleProjectExclusion('${escAttr(p.project_name)}', this.checked)">
        </td>
        <td style="text-align:center">
          <input type="checkbox" ${p.special ? 'checked' : ''}
                 onchange="adminToggleProjectSpecial('${escAttr(p.project_name)}', this.checked)">
        </td>
        <td style="text-align:center">
          <input type="checkbox" ${p.track ? 'checked' : ''}
                 onchange="adminToggleProjectTrack('${escAttr(p.project_name)}', this.checked)">
        </td>
        <td>
          <button class="btn-logout" onclick="adminRemoveProjectSetting('${escAttr(p.project_name)}')">Remove</button>
        </td>
      </tr>`).join('') || `
      <tr><td colspan="5" style="color:#475569;text-align:center;padding:0.75rem 0;font-style:italic">
        No project settings. Add a project name below.
      </td></tr>`;
```

(Note the `colspan="4"` → `colspan="5"` on the empty-state row.)

- [ ] **Step 2: Extend the card header — add a Track count badge and a Track description**

In `static/index.html`, find the card-title block in `renderAdminProjects` (line 2737–2746). It currently reads:

```javascript
    document.getElementById('admin-projects').innerHTML = `
      <div class="card full">
        <div class="card-title">Project Settings
          <span class="badge badge-muted" style="margin-left:0.4rem">${list.filter(p => p.price_excluded).length} fixed price</span>
          <span class="badge badge-muted" style="margin-left:0.4rem">${list.filter(p => p.special).length} special</span>
        </div>
        <p style="color:#64748b;font-size:0.75rem;margin-bottom:0.75rem">
          <b>Fixed Price</b> exempts a project from the Jita 4-4 price-difference warnings.
          <b>Special</b> shows the top contributors on the project card and the full
          participant list in its details popup.
        </p>
```

Change it to:

```javascript
    document.getElementById('admin-projects').innerHTML = `
      <div class="card full">
        <div class="card-title">Project Settings
          <span class="badge badge-muted" style="margin-left:0.4rem">${list.filter(p => p.price_excluded).length} fixed price</span>
          <span class="badge badge-muted" style="margin-left:0.4rem">${list.filter(p => p.special).length} special</span>
          <span class="badge badge-muted" style="margin-left:0.4rem">${list.filter(p => p.track).length} track</span>
        </div>
        <p style="color:#64748b;font-size:0.75rem;margin-bottom:0.75rem">
          <b>Fixed Price</b> exempts a project from the Jita 4-4 price-difference warnings.
          <b>Special</b> shows the top contributors on the project card and the full
          participant list in its details popup.
          <b>Track</b> pins a project to the top of the Projects page sort order.
        </p>
```

- [ ] **Step 3: Extend the table header — add a Track `<th>`**

In `static/index.html`, find the table head in `renderAdminProjects` (line 2754–2760). It currently reads:

```javascript
        <table>
          <thead><tr>
            <th>Project Name</th>
            <th style="text-align:center;width:110px">Fixed Price</th>
            <th style="text-align:center;width:90px">Special</th>
            <th style="width:100px"></th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>
```

Change it to:

```javascript
        <table>
          <thead><tr>
            <th>Project Name</th>
            <th style="text-align:center;width:110px">Fixed Price</th>
            <th style="text-align:center;width:90px">Special</th>
            <th style="text-align:center;width:80px">Track</th>
            <th style="width:100px"></th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>
```

- [ ] **Step 4: Add the `adminToggleProjectTrack` JS function**

In `static/index.html`, find `adminToggleProjectSpecial` (near line 2786). Immediately **after** that function (before `adminToggleProjectExclusion`), insert:

```javascript
  async function adminToggleProjectTrack(name, track) {
    try {
      const r = await fetch('/api/admin/projects/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, track }),
      });
      if (!r.ok) showError('admin-error', 'Toggle failed.');
    } catch { showError('admin-error', 'Network error.'); }
  }
```

- [ ] **Step 5: Run the existing test suite to confirm no regression**

Run: `pytest -q`
Expected: PASS — all tests pass.

- [ ] **Step 6: Manual UI verification**

Start the app per the project's "Running the app locally" memory:

```bash
set -a && source .env && set +a && ~/.local/bin/uvicorn main:app --port 8000
```

Then in a browser:
1. Log in as an admin account, open the Admin page.
2. Confirm the Project Settings card shows a third column "Track" with checkboxes, and a "N track" badge in the header.
3. Toggle Track on a row. Reload the page; the checkbox stays in the new state.
4. Open the Projects page; a tracked project shows the purple "Track" badge and sorts above non-tracked ones when sorting by Priority.

- [ ] **Step 7: Commit**

```bash
git add static/index.html
git commit -m "$(cat <<'EOF'
feat: admin project-settings "Track" toggle

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Admin UI — native `<datalist>` autocomplete on the project-name input

**Files:**
- Modify: `static/index.html` (the input markup in `renderAdminProjects` near line 2747–2753; the `loadAdminProjects` function near line 2708–2714)

This task has no automated frontend tests — verify manually after wiring.

- [ ] **Step 1: Add the `list=...` attribute on the input and a `<datalist>` element**

In `static/index.html`, find the input + button block inside `renderAdminProjects` (line 2747–2753). It currently reads:

```javascript
        <div style="display:flex;gap:0.5rem;margin-bottom:0.75rem">
          <input type="text" id="admin-proj-add" placeholder="Project name (e.g. Armor Remote Repair)"
                 style="flex:1;background:#252a3d;border:1px solid #2d3348;color:#e2e8f0;
                        padding:0.4rem 0.6rem;border-radius:6px;font-size:0.8rem;font-family:inherit"
                 onkeydown="if(event.key==='Enter') adminAddProject()">
          <button class="btn-action" onclick="adminAddProject()">Add</button>
        </div>
```

Change it to:

```javascript
        <div style="display:flex;gap:0.5rem;margin-bottom:0.75rem">
          <input type="text" id="admin-proj-add" placeholder="Project name (e.g. Armor Remote Repair)"
                 list="admin-proj-names" autocomplete="off"
                 style="flex:1;background:#252a3d;border:1px solid #2d3348;color:#e2e8f0;
                        padding:0.4rem 0.6rem;border-radius:6px;font-size:0.8rem;font-family:inherit"
                 onkeydown="if(event.key==='Enter') adminAddProject()">
          <datalist id="admin-proj-names"></datalist>
          <button class="btn-action" onclick="adminAddProject()">Add</button>
        </div>
```

- [ ] **Step 2: Fetch available names in `loadAdminProjects` and populate the datalist**

In `static/index.html`, find `loadAdminProjects` (line 2708). It currently reads:

```javascript
  async function loadAdminProjects() {
    try {
      const r = await fetch('/api/admin/projects/settings');
      if (!r.ok) return;
      renderAdminProjects(await r.json());
    } catch {}
  }
```

Change it to:

```javascript
  async function loadAdminProjects() {
    try {
      const r = await fetch('/api/admin/projects/settings');
      if (!r.ok) return;
      renderAdminProjects(await r.json());
      // Best-effort: populate the autocomplete after the DOM exists. A failure
      // here must not break the admin panel — the input still works as plain
      // text without suggestions.
      try {
        const ar = await fetch('/api/admin/projects/available');
        if (!ar.ok) return;
        const { names = [] } = await ar.json();
        const dl = document.getElementById('admin-proj-names');
        if (dl) {
          dl.innerHTML = names.map(n => `<option value="${escAttr(n)}">`).join('');
        }
      } catch {}
    } catch {}
  }
```

- [ ] **Step 3: Manual UI verification**

Start the app per the "Running the app locally" memory:

```bash
set -a && source .env && set +a && ~/.local/bin/uvicorn main:app --port 8000
```

Then in a browser:
1. Log in as an admin account, open the Admin page.
2. Click the "Project name" input. The browser shows a list of project names (e.g. "Compressed Gneiss", "Neural Network Analyzer", …) sourced from the corp's ESI projects.
3. Type "neur" — the suggestions narrow to "Neural Network Analyzer".
4. Pick a suggestion and click Add → the new row appears in the table with all three flags off (the existing add-flow is unchanged).
5. (Negative path) Block the network briefly or kill ESI on the server: reload Admin — the input still works as plain text, no error toast.

- [ ] **Step 4: Run the full test suite once more**

Run: `pytest -q`
Expected: PASS — every test passes.

- [ ] **Step 5: Commit**

```bash
git add static/index.html
git commit -m "$(cat <<'EOF'
feat: autocomplete the admin project-name input from ESI corp projects

Uses a native <datalist> populated by GET /api/admin/projects/available.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Done

All six tasks are independent commits. After Task 6:

- `git log --oneline -7` should show the six new commits plus the spec commit.
- `pytest -q` passes.
- The admin Project Settings panel has Fixed Price + Special + Track columns; legacy track names ("Remote Boost Shield", "Armor Remote Repair") are pre-flagged as track on existing production DBs (seeded by the migration) and unchanged on fresh DBs.
- Typing in the "Project name" input shows ESI-sourced suggestions.
