# Special Projects Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an admin mark projects as "special"; special projects show their top 3 contributors on the card and the full participant list (plus the pie) in the details modal.

**Architecture:** A `special` flag on the existing `project_settings` table, toggled via the admin panel. `/api/projects` flags each project and, for special ones, attaches the top 3 contributors (best-effort, server-cached). The frontend renders contributor rows on special cards and the full list in special modals.

**Tech Stack:** FastAPI + `aiosqlite` (Python), plain HTML/CSS/JS in `static/index.html`, `pytest` + `respx`.

---

## Preconditions

- Branch: work on `master`; commit there.
- `respx` / `pytest` are available. Run the app locally with: `set -a && source .env && set +a && ~/.local/bin/uvicorn main:app --port <PORT>`.
- `static/index.html` is currently clean; `main.py` / `db.py` are clean.

## File Structure

- **Modify** `db.py` — `special` column on `project_settings` (+ migration), `list_special_projects`, `set_project_special`, extended `list_project_settings`.
- **Modify** `main.py` — admin POST handles `special`; `_contributors_cache` + `_cached_project_contributors`; `/api/projects` flags `special` + attaches `top_contributors`.
- **Modify** `static/index.html` — admin "Special" toggle; special-card contributor rows; modal full list.
- **Create** `tests/test_special_projects.py` — DB + endpoint tests.

---

## Task 1: `special` column + DB accessors

**Files:**
- Modify: `db.py` (the `project_settings` CREATE TABLE near line 81; a migration in `init_db`; accessors near line 243-277)
- Test: `tests/test_special_projects.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_special_projects.py` with exactly this content:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_special_projects.py -v`
Expected: FAIL — `ImportError: cannot import name 'set_project_special' from 'db'`.

- [ ] **Step 3: Add `special` to the `project_settings` table**

In `db.py`, change the `project_settings` CREATE TABLE (near line 81) from:

```python
        await db.execute("""
            CREATE TABLE IF NOT EXISTS project_settings (
                project_name   TEXT PRIMARY KEY,
                price_excluded INTEGER DEFAULT 0,
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
                updated_at     REAL NOT NULL
            )
        """)
```

- [ ] **Step 4: Add the migration for pre-existing databases**

In `db.py`, find the seed comment line `        # One-time seed of NPC-priced project names — only when table is empty,` and insert this block immediately **before** it:

```python
        # Migration: add `special` to project_settings on pre-existing DBs.
        async with db.execute("PRAGMA table_info(project_settings)") as cur:
            _cols = {r[1] for r in await cur.fetchall()}
        if "special" not in _cols:
            await db.execute(
                "ALTER TABLE project_settings ADD COLUMN special INTEGER DEFAULT 0")
```

- [ ] **Step 5: Add the accessors**

In `db.py`, immediately after the `delete_project_setting` function (it ends near line 277 with `await db.commit()`), add:

```python


async def list_special_projects() -> set[str]:
    async with aiosqlite.connect(_db_path()) as db:
        async with db.execute(
            "SELECT project_name FROM project_settings WHERE special = 1"
        ) as cur:
            return {r[0] for r in await cur.fetchall()}


async def set_project_special(name: str, special: bool):
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            """INSERT INTO project_settings (project_name, special, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(project_name) DO UPDATE SET
                 special    = excluded.special,
                 updated_at = excluded.updated_at""",
            (name, 1 if special else 0, time.time()))
        await db.commit()
```

- [ ] **Step 6: Extend `list_project_settings`**

In `db.py`, in `list_project_settings`, change the SELECT from:

```python
            "SELECT project_name, price_excluded, updated_at "
            "FROM project_settings ORDER BY project_name"
```

to:

```python
            "SELECT project_name, price_excluded, special, updated_at "
            "FROM project_settings ORDER BY project_name"
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `pytest tests/test_special_projects.py -v`
Expected: PASS — 3 passed.

- [ ] **Step 8: Commit**

```bash
git add db.py tests/test_special_projects.py
git commit -m "$(cat <<'EOF'
feat: add `special` flag to project_settings

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Admin endpoint accepts `special`

**Files:**
- Modify: `main.py` (the `admin_set_project_setting` handler near line 444)
- Test: `tests/test_special_projects.py` (append)

- [ ] **Step 1: Write the failing tests**

Append exactly this to `tests/test_special_projects.py`:

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


async def test_admin_set_special_requires_admin(client):
    cookies = await _admin_cookie(555)  # not in ADMIN_CHARACTER_IDS
    r = await client.post("/api/admin/projects/settings",
                          json={"name": "Armor Remote Repair", "special": True},
                          cookies=cookies)
    assert r.status_code == 403


async def test_admin_set_special_flag(client, monkeypatch):
    monkeypatch.setenv("ADMIN_CHARACTER_IDS", "777")
    cookies = await _admin_cookie(777)
    r = await client.post("/api/admin/projects/settings",
                          json={"name": "Armor Remote Repair", "special": True},
                          cookies=cookies)
    assert r.status_code == 200
    assert "Armor Remote Repair" in await list_special_projects()


async def test_admin_set_special_leaves_price_excluded(client, monkeypatch):
    monkeypatch.setenv("ADMIN_CHARACTER_IDS", "777")
    cookies = await _admin_cookie(777)
    await client.post("/api/admin/projects/settings",
                      json={"name": "P1", "excluded": True}, cookies=cookies)
    await client.post("/api/admin/projects/settings",
                      json={"name": "P1", "special": True}, cookies=cookies)
    settings = {s["project_name"]: s for s in await list_project_settings()}
    assert settings["P1"]["price_excluded"] == 1
    assert settings["P1"]["special"] == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_special_projects.py -v`
Expected: the 3 new tests FAIL — `test_admin_set_special_flag` / `test_admin_set_special_leaves_price_excluded` fail because the endpoint ignores `special` (the project never appears in `list_special_projects()`).

- [ ] **Step 3: Update the admin handler in `main.py`**

In `main.py`, replace the `admin_set_project_setting` handler (near line 444):

```python
@app.post("/api/admin/projects/settings")
async def admin_set_project_setting(body: dict, _admin: int = Depends(require_admin)):
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name required")
    excluded = bool(body.get("excluded", True))
    await database.set_project_price_excluded(name, excluded)
    return {"ok": True}
```

with:

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

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_special_projects.py -v`
Expected: PASS — 6 passed.

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_special_projects.py
git commit -m "$(cat <<'EOF'
feat: admin projects endpoint accepts the `special` flag

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Contributors cache + `/api/projects` enrichment

**Files:**
- Modify: `main.py` (cache helper before the `/api/projects` route at line 605; the `/api/projects` tail near line 662; the `/api/projects/{id}/contributors` route near line 814)
- Test: `tests/test_special_projects.py` (append)

- [ ] **Step 1: Write the failing tests**

Append exactly this to `tests/test_special_projects.py`:

```python
import respx
from httpx import Response

CORP = "98340844"  # conftest sets CORP_ID=98340844
PROJECTS_URL = f"https://esi.evetech.net/corporations/{CORP}/projects"
IDS_URL = "https://esi.evetech.net/latest/universe/ids/"


def _contrib_url(project_id: str) -> str:
    return f"https://esi.evetech.net/corporations/{CORP}/projects/{project_id}/contributors"


@respx.mock
async def test_project_contributors_endpoint_caches(client):
    route = respx.get(_contrib_url("p1")).mock(return_value=Response(200, json={
        "contributors": [{"id": 1, "name": "Alice", "contributed": 10}],
    }))
    cookies = await _admin_cookie(777)
    await client.get("/api/projects/p1/contributors", cookies=cookies)
    await client.get("/api/projects/p1/contributors", cookies=cookies)
    assert route.call_count == 1


@respx.mock
async def test_projects_marks_special_and_top_contributors(client):
    await set_project_special("Armor Remote Repair", True)
    respx.get(PROJECTS_URL).mock(return_value=Response(200, json={"projects": [
        {"id": "p1", "name": "Armor Remote Repair", "state": "Active",
         "progress": {"current": 5, "desired": 10}},
    ]}))
    respx.post(IDS_URL).mock(return_value=Response(200, json={}))
    respx.get(_contrib_url("p1")).mock(return_value=Response(200, json={"contributors": [
        {"id": 1, "name": "Alice", "contributed": 100},
        {"id": 2, "name": "Bob", "contributed": 300},
        {"id": 3, "name": "Carol", "contributed": 50},
        {"id": 4, "name": "Dave", "contributed": 200},
    ]}))
    cookies = await _admin_cookie(777)
    r = await client.get("/api/projects", cookies=cookies)
    assert r.status_code == 200
    proj = next(p for p in r.json()["active"] if p["name"] == "Armor Remote Repair")
    assert proj["special"] is True
    assert [c["name"] for c in proj["top_contributors"]] == ["Bob", "Dave", "Alice"]
```

(If `respx` reports an unmatched request when you run this, the `/api/projects` route hit an ESI endpoint not mocked here — add a `respx` mock for that URL returning an empty `200` and continue.)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_special_projects.py -v`
Expected: the 2 new tests FAIL — `test_project_contributors_endpoint_caches` fails (`call_count == 2`, no cache); `test_projects_marks_special_and_top_contributors` fails (`KeyError: 'special'` or `'top_contributors'`).

- [ ] **Step 3: Add the contributors cache helper in `main.py`**

In `main.py`, immediately **before** the `@app.get("/api/projects")` route (line 605), add:

```python
# Project contributors change slowly; cache per project for 10 min.
_contributors_cache: dict[str, tuple[float, list]] = {}
CONTRIBUTORS_TTL = 600


async def _cached_project_contributors(project_id: str, access_token: str) -> list:
    cached = _contributors_cache.get(project_id)
    if cached and (time.time() - cached[0]) < CONTRIBUTORS_TTL:
        return cached[1]
    contribs = await get_corp_project_contributors(CORP_ID, project_id, access_token)
    _contributors_cache[project_id] = (time.time(), contribs)
    return contribs


```

(`time`, `CORP_ID`, and `get_corp_project_contributors` are already in scope in `main.py`.)

- [ ] **Step 4: Flag `special` and attach `top_contributors` in `/api/projects`**

In `main.py`, in the `/api/projects` route, replace this block (near line 662):

```python
    excluded_names = await database.list_excluded_projects()
    for p in active + closed:
        _compute_project_warn(p, excluded_names)
        p["sort_priority"] = _project_priority(p)
```

with:

```python
    excluded_names = await database.list_excluded_projects()
    special_names = await database.list_special_projects()
    for p in active + closed:
        _compute_project_warn(p, excluded_names)
        p["sort_priority"] = _project_priority(p)
        p["special"] = (p.get("name") or "") in special_names

    # Special projects show their top 3 contributors on the card — best-effort,
    # so a contributor-fetch failure never breaks the projects list.
    special_projects = [p for p in active + closed if p["special"]]
    if special_projects:
        contrib_results = await asyncio.gather(*[
            _safe(_cached_project_contributors(p["id"], access_token))
            for p in special_projects
        ])
        for p, (contribs, _err) in zip(special_projects, contrib_results):
            if contribs:
                top = sorted(contribs, key=lambda c: c.get("contributed") or 0,
                             reverse=True)[:3]
                p["top_contributors"] = [
                    {"id": c.get("id"), "name": c.get("name") or ""} for c in top
                ]
```

(`asyncio`, `_safe`, and `access_token` are already in scope here.)

- [ ] **Step 5: Switch the contributors route to the cache**

In `main.py`, in the `project_contributors` route (near line 814), change:

```python
        contributors = await get_corp_project_contributors(CORP_ID, project_id, access_token)
```

to:

```python
        contributors = await _cached_project_contributors(project_id, access_token)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `pytest tests/test_special_projects.py -v`
Expected: PASS — 8 passed.

- [ ] **Step 7: Run the full test suite**

Run: `pytest -q`
Expected: all tests pass, no regressions.

- [ ] **Step 8: Commit**

```bash
git add main.py tests/test_special_projects.py
git commit -m "$(cat <<'EOF'
feat: flag special projects and attach top contributors in /api/projects

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Admin UI — "Special" toggle

**Files:**
- Modify: `static/index.html` — replace `renderAdminProjects` and `adminAddProjectExclusion`; add `adminToggleProjectSpecial`.

No automated test — verified by the sanity check in Step 4.

- [ ] **Step 1: Replace `renderAdminProjects`**

In `static/index.html`, replace the entire `renderAdminProjects` function (it starts `function renderAdminProjects(data) {` and ends at its closing `}` before `async function adminAddProjectExclusion`) with:

```javascript
  function renderAdminProjects(data) {
    const list = data.projects || [];
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
        <div style="display:flex;gap:0.5rem;margin-bottom:0.75rem">
          <input type="text" id="admin-proj-add" placeholder="Project name (e.g. Armor Remote Repair)"
                 style="flex:1;background:#252a3d;border:1px solid #2d3348;color:#e2e8f0;
                        padding:0.4rem 0.6rem;border-radius:6px;font-size:0.8rem;font-family:inherit"
                 onkeydown="if(event.key==='Enter') adminAddProject()">
          <button class="btn-action" onclick="adminAddProject()">Add</button>
        </div>
        <table>
          <thead><tr>
            <th>Project Name</th>
            <th style="text-align:center;width:110px">Fixed Price</th>
            <th style="text-align:center;width:90px">Special</th>
            <th style="width:100px"></th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;
  }
```

- [ ] **Step 2: Replace `adminAddProjectExclusion` with `adminAddProject`**

In `static/index.html`, replace the entire `adminAddProjectExclusion` function with:

```javascript
  async function adminAddProject() {
    const input = document.getElementById('admin-proj-add');
    const name = input.value.trim();
    if (!name) return;
    try {
      const r = await fetch('/api/admin/projects/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, excluded: false }),
      });
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        showError('admin-error', 'Add failed: ' + (d.detail || r.status));
        return;
      }
      input.value = '';
      loadAdminProjects();
    } catch { showError('admin-error', 'Network error.'); }
  }

  async function adminToggleProjectSpecial(name, special) {
    try {
      const r = await fetch('/api/admin/projects/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, special }),
      });
      if (!r.ok) showError('admin-error', 'Toggle failed.');
    } catch { showError('admin-error', 'Network error.'); }
  }
```

(`adminToggleProjectExclusion` and `adminRemoveProjectSetting` are unchanged. The old `adminAddProjectExclusion` name is now gone; its only callers were the Add button / input in `renderAdminProjects`, which Step 1 already updated to `adminAddProject()`.)

- [ ] **Step 3: Verify no stale references remain**

Run:
```bash
grep -c "adminAddProjectExclusion" static/index.html
grep -c "adminToggleProjectSpecial" static/index.html
```
Expected: `0` (the old name is fully gone), then `2` (the function definition + the `onchange` in the table row).

- [ ] **Step 4: Sanity-check the page serves**

Run:
```bash
set -a && source .env && set +a && ~/.local/bin/uvicorn main:app --port 8031 >/tmp/uv.log 2>&1 &
sleep 4
curl -s -o /dev/null -w "page=%{http_code}\n" http://localhost:8031/
pkill -f "uvicorn main:app --port 8031"
```
Expected: `page=200`.

- [ ] **Step 5: Commit**

```bash
git add static/index.html
git commit -m "$(cat <<'EOF'
feat: admin project-settings "Special" toggle

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Project card — top-3 contributor rows

**Files:**
- Modify: `static/index.html` — `projectCard` (near line 1909-1974)

No automated test — verified by the sanity check in Step 3.

- [ ] **Step 1: Add the `specialContribs` block to `projectCard`**

In `static/index.html`, inside `projectCard`, find the `marketBlock` constant assignment (it ends with `</div>\` : '';` near line 1954). Immediately **after** that statement and before the `return \`` line, add:

```javascript
    const specialContribs = (p.special && p.top_contributors && p.top_contributors.length)
      ? `<div style="margin-top:0.75rem;display:flex;flex-direction:column;gap:0.4rem">
           ${p.top_contributors.map(c => `
             <div style="display:flex;align-items:center;gap:0.5rem">
               <img src="https://images.evetech.net/characters/${c.id}/portrait?size=64"
                    alt="" style="width:28px;height:28px;border-radius:50%;background:#252a3d;flex:0 0 auto">
               <span style="color:#e2e8f0;font-size:0.8rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1 1 auto">${escHtml(c.name || 'Unknown')}</span>
               ${characterZkillLink(c.id)}
             </div>`).join('')}
         </div>`
      : '';
```

- [ ] **Step 2: Render `specialContribs` beneath the progress bar**

In `static/index.html`, in the `projectCard` return template, find the progress-bar block followed by `${reasonsBlock}`:

```javascript
          <div class="progress-bar"><div class="progress-fill" style="width:${pct}%"></div></div>
        </div>
        ${reasonsBlock}
```

Replace it with:

```javascript
          <div class="progress-bar"><div class="progress-fill" style="width:${pct}%"></div></div>
        </div>
        ${specialContribs}
        ${reasonsBlock}
```

This places the contributor rows directly beneath the progress bar. Non-special projects (and special projects with no contributors) render nothing — `specialContribs` is `''`.

- [ ] **Step 3: Sanity-check the page serves**

Run:
```bash
set -a && source .env && set +a && ~/.local/bin/uvicorn main:app --port 8032 >/tmp/uv.log 2>&1 &
sleep 4
curl -s -o /dev/null -w "page=%{http_code}\n" http://localhost:8032/
curl -s http://localhost:8032/ | grep -c "specialContribs"
pkill -f "uvicorn main:app --port 8032"
```
Expected: `page=200`, then `2` (the constant definition + its use in the return template).

- [ ] **Step 4: Commit**

```bash
git add static/index.html
git commit -m "$(cat <<'EOF'
feat: show top-3 contributor rows on special project cards

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Details modal — full list for special projects

**Files:**
- Modify: `static/index.html` — `renderContributors` (near line 1995); add `renderContributorList`

No automated test — verified by the sanity check in Step 3.

- [ ] **Step 1: Append the full list for special projects in `renderContributors`**

In `static/index.html`, in `renderContributors`, find the final return line:

```javascript
    return header + renderContributorPie(sorted, totalContributed);
  }
```

Replace it with:

```javascript
    return header + renderContributorPie(sorted, totalContributed)
         + (project.special ? renderContributorList(sorted, projTotal) : '');
  }

  // Full ranked participant list — shown below the pie for special projects.
  function renderContributorList(sorted, projTotal) {
    const rows = sorted.map((c, i) => {
      const pct = projTotal > 0 ? (c.contributed / projTotal * 100) : 0;
      const isoLast = c.last_modified ? new Date(c.last_modified).toLocaleDateString() : '';
      return `
        <div style="display:grid;grid-template-columns:24px 36px 1fr auto;gap:0.75rem;
                    align-items:center;padding:0.5rem 0;border-bottom:1px solid #1a1e2e">
          <span style="color:#475569;font-size:0.75rem;text-align:right;font-variant-numeric:tabular-nums">${i + 1}</span>
          <img src="https://images.evetech.net/characters/${c.id}/portrait?size=64"
               alt="" style="width:32px;height:32px;border-radius:50%;background:#252a3d">
          <div style="min-width:0">
            <div style="color:#e2e8f0;font-weight:500;display:flex;align-items:center;gap:0.4rem">
              <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escHtml(c.name || 'Unknown')}</span>
              ${characterZkillLink(c.id)}
            </div>
            <div style="height:4px;background:#252a3d;border-radius:2px;margin-top:0.3rem;overflow:hidden">
              <div style="height:100%;background:#38bdf8;width:${pct.toFixed(1)}%"></div>
            </div>
            ${isoLast ? `<div style="font-size:0.65rem;color:#475569;margin-top:0.15rem">last contribution ${isoLast}</div>` : ''}
          </div>
          <div style="text-align:right">
            <div style="color:#38bdf8;font-weight:600;font-size:0.85rem">${fmtNum(c.contributed)}</div>
            <div style="color:#64748b;font-size:0.7rem">${pct.toFixed(1)}%</div>
          </div>
        </div>`;
    }).join('');
    return `
      <div style="margin-top:1rem;padding-top:0.75rem;border-top:1px solid #2d3348">
        <div style="font-size:0.7rem;font-weight:600;color:#64748b;text-transform:uppercase;
                    letter-spacing:0.08em;margin-bottom:0.5rem">All participants</div>
        ${rows}
      </div>`;
  }
```

`project`, `sorted`, `projTotal`, and `totalContributed` already exist in `renderContributors`. `escHtml`, `fmtNum`, and `characterZkillLink` are existing helpers.

- [ ] **Step 2: Sanity-check the page serves**

Run:
```bash
set -a && source .env && set +a && ~/.local/bin/uvicorn main:app --port 8033 >/tmp/uv.log 2>&1 &
sleep 4
curl -s -o /dev/null -w "page=%{http_code}\n" http://localhost:8033/
curl -s http://localhost:8033/ | grep -c "renderContributorList"
pkill -f "uvicorn main:app --port 8033"
```
Expected: `page=200`, then `2` (the function definition + its call in `renderContributors`).

- [ ] **Step 3: Commit**

```bash
git add static/index.html
git commit -m "$(cat <<'EOF'
feat: show the full participant list in special project modals

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Out of Scope / Follow-up

- Deploy: the user runs `./deploy.sh` separately.
- Not included: contributor icons on non-special cards, configurable row count, a dedicated special-projects view.

## Self-Review

- **Spec coverage:** `special` column + migration + accessors → Task 1; admin endpoint `special` field → Task 2; contributors cache → Task 3 (Steps 3, 5); `/api/projects` `special` flag + `top_contributors` (best-effort) → Task 3 Step 4; admin "Special" toggle UI → Task 4; card top-3 contributor rows → Task 5; modal pie + full list → Task 6. All spec sections mapped.
- **Placeholder scan:** no TBD/TODO; every code step is complete.
- **Type consistency:** `set_project_special` / `list_special_projects` defined in Task 1, used in Task 2 (`database.set_project_special`) and Task 3 (`database.list_special_projects`); `_cached_project_contributors` defined in Task 3 Step 3, used in Step 4 and Step 5; `p["special"]` set in Task 3 Step 4 is read by `projectCard` (`p.special`, Task 5) and `renderContributors` (`project.special`, Task 6); `p["top_contributors"]` (list of `{id, name}`) set in Task 3 Step 4 matches `projectCard`'s `c.id` / `c.name` use in Task 5; `adminAddProject` / `adminToggleProjectSpecial` defined in Task 4 Step 2 match the `onclick` / `onchange` references written in Task 4 Step 1.
