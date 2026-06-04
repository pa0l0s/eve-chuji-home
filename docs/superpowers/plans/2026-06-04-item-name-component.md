# Shared Item-Name Component Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render every in-game item/type name through one shared component that shows the name plus an "info" and a "market" icon, each opening the corresponding in-game window via ESI.

**Architecture:** Two new FastAPI proxy routes mirror the existing `/api/ui/open-contract/{id}` pattern, POSTing to ESI `openwindow/information` and `openwindow/marketdetails` with the caller's token. The frontend gains one `itemName(typeId, name)` helper (template-literal HTML string, matching the existing style) plus two click handlers reusing the `openContractInEve` feedback pattern; all eight item-name render sites call the helper.

**Tech Stack:** FastAPI + httpx (backend), plain JS in `static/index.html` (frontend), pytest + respx (tests).

---

## File Structure

- `esi.py` — add `open_information_in_client`, `open_market_in_client` (mirror `open_contract_in_client`).
- `main.py` — add two routes `POST /api/ui/open-information/{type_id}` and `POST /api/ui/open-market/{type_id}`; extend the `from esi import (...)` block.
- `static/info-icon.png`, `static/market-icon.png` — moved from repo root (`git mv`).
- `static/index.html` — add CSS, `itemName()` helper, `openItemInfo`/`openItemMarket` handlers; update eight call sites.
- `tests/test_open_window.py` — new test file for the two routes.

---

## Task 1: Move icon assets into `static/`

**Files:**
- Move: `info-icon_no_background.png` → `static/info-icon.png`
- Move: `market-icon.png` → `static/market-icon.png`

- [ ] **Step 1: Move both files with git**

```bash
cd /mnt/d/Source/WEB/claude-eve-chuji-homepage
git mv info-icon_no_background.png static/info-icon.png
git mv market-icon.png static/market-icon.png
```

- [ ] **Step 2: Verify they are tracked in the new location**

Run: `git status --short && ls static/info-icon.png static/market-icon.png`
Expected: both files listed as renamed/staged; `ls` prints both paths with no error.

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "chore: move item info/market icons into static/"
```

---

## Task 2: Backend ESI helper functions

**Files:**
- Modify: `esi.py` (add after `open_contract_in_client`, which ends at line ~572)

- [ ] **Step 1: Add the two functions**

Add immediately after the `open_contract_in_client` function in `esi.py`:

```python
async def open_information_in_client(type_id: int, access_token: str) -> None:
    """Open the in-game Show Info window for a type in the authenticated character's client."""
    async with httpx.AsyncClient() as client:
        r = await client.post(
            "https://esi.evetech.net/ui/openwindow/information",
            params={"target_id": type_id},
            headers={
                "Authorization": f"Bearer {access_token}",
                "X-Compatibility-Date": "2026-01-01",
            },
        )
        r.raise_for_status()


async def open_market_in_client(type_id: int, access_token: str) -> None:
    """Open the in-game Market Details window for a type in the authenticated character's client."""
    async with httpx.AsyncClient() as client:
        r = await client.post(
            "https://esi.evetech.net/ui/openwindow/marketdetails",
            params={"type_id": type_id},
            headers={
                "Authorization": f"Bearer {access_token}",
                "X-Compatibility-Date": "2026-01-01",
            },
        )
        r.raise_for_status()
```

> Note: ESI `information` uses the `target_id` query param; `marketdetails` uses `type_id`. This is intentional, not a typo.

- [ ] **Step 2: Verify the module still imports**

Run: `python -c "import esi; print(esi.open_information_in_client, esi.open_market_in_client)"`
Expected: prints two `<function ...>` reprs, no error.

- [ ] **Step 3: Commit**

```bash
git add esi.py
git commit -m "feat: ESI helpers to open type info/market windows in client"
```

---

## Task 3: Backend routes (TDD)

**Files:**
- Test: `tests/test_open_window.py` (create)
- Modify: `main.py` — `from esi import (...)` block (around line 25) and add routes after `open_contract` (ends ~line 93)

- [ ] **Step 1: Write the failing test file**

Create `tests/test_open_window.py`:

```python
import time

import pytest
import respx
from httpx import Response

from db import init_db, upsert_token
from auth import make_session_cookie

INFO_URL = "https://esi.evetech.net/ui/openwindow/information"
MARKET_URL = "https://esi.evetech.net/ui/openwindow/marketdetails"


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


async def test_open_information_requires_auth(client):
    r = await client.post("/api/ui/open-information/34")
    assert r.status_code == 401


async def test_open_market_requires_auth(client):
    r = await client.post("/api/ui/open-market/34")
    assert r.status_code == 401


@respx.mock
async def test_open_information_happy_path(client):
    route = respx.post(INFO_URL).mock(return_value=Response(204))
    cookies = await _auth_cookie()
    r = await client.post("/api/ui/open-information/587", cookies=cookies)
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert route.called
    assert route.calls.last.request.url.params["target_id"] == "587"


@respx.mock
async def test_open_market_happy_path(client):
    route = respx.post(MARKET_URL).mock(return_value=Response(204))
    cookies = await _auth_cookie()
    r = await client.post("/api/ui/open-market/587", cookies=cookies)
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert route.called
    assert route.calls.last.request.url.params["type_id"] == "587"


@respx.mock
async def test_open_information_403_maps_to_scope_message(client):
    respx.post(INFO_URL).mock(return_value=Response(403, json={}))
    cookies = await _auth_cookie()
    r = await client.post("/api/ui/open-information/587", cookies=cookies)
    assert r.status_code == 403
    assert "esi-ui.open_window.v1" in r.json()["detail"]


@respx.mock
async def test_open_market_esi_failure_maps_to_502(client):
    respx.post(MARKET_URL).mock(return_value=Response(500, json={}))
    cookies = await _auth_cookie()
    r = await client.post("/api/ui/open-market/587", cookies=cookies)
    assert r.status_code == 502
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_open_window.py -v`
Expected: the two `requires_auth` tests may pass (no route → 404, NOT 401 — so they FAIL too). All tests FAIL — happy-path tests get 404 (route undefined).

- [ ] **Step 3: Extend the esi import in `main.py`**

Find the import block near line 25:

```python
    get_corp_contracts, get_corp_contract_items, get_ship_type_ids,
```

There is an existing import of `open_contract_in_client` further down the same `from esi import (...)` parenthesised block. Locate the line containing `open_contract_in_client` and add the two new names next to it. For example, change:

```python
    open_contract_in_client,
```

to:

```python
    open_contract_in_client, open_information_in_client, open_market_in_client,
```

(If `open_contract_in_client` is grouped differently, just ensure all three names appear in the `from esi import (...)` block.)

- [ ] **Step 4: Add the two routes in `main.py`**

Add immediately after the `open_contract` route (after its `return {"ok": True}`, around line 93):

```python
@app.post("/api/ui/open-information/{type_id}")
async def open_information(type_id: int, session: str | None = Cookie(None)):
    """Open the in-game Show Info window for a type in the user's EVE client."""
    character_id = await get_current_character_id(session)
    try:
        access_token = await get_valid_token(character_id)
        await open_information_in_client(type_id, access_token)
    except httpx.HTTPStatusError as e:
        print(f"ESI open-information HTTP {e.response.status_code}: {e.response.text}")
        if e.response.status_code == 403:
            raise HTTPException(status_code=403, detail="Missing scope: esi-ui.open_window.v1")
        raise HTTPException(status_code=502, detail="ESI unavailable")
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="ESI unavailable")
    return {"ok": True}


@app.post("/api/ui/open-market/{type_id}")
async def open_market(type_id: int, session: str | None = Cookie(None)):
    """Open the in-game Market Details window for a type in the user's EVE client."""
    character_id = await get_current_character_id(session)
    try:
        access_token = await get_valid_token(character_id)
        await open_market_in_client(type_id, access_token)
    except httpx.HTTPStatusError as e:
        print(f"ESI open-market HTTP {e.response.status_code}: {e.response.text}")
        if e.response.status_code == 403:
            raise HTTPException(status_code=403, detail="Missing scope: esi-ui.open_window.v1")
        raise HTTPException(status_code=502, detail="ESI unavailable")
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="ESI unavailable")
    return {"ok": True}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_open_window.py -v`
Expected: all 6 tests PASS.

- [ ] **Step 6: Run the full suite to confirm nothing broke**

Run: `pytest -q`
Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add main.py tests/test_open_window.py
git commit -m "feat: routes to open type info/market windows in EVE client"
```

---

## Task 4: Frontend component — CSS, helper, handlers

**Files:**
- Modify: `static/index.html`

There is no JS test harness; this task is verified by reading the rendered output and a manual smoke test (Task 6).

- [ ] **Step 1: Add CSS**

Find the existing rule `.mail-fitting-link { text-decoration: underline; cursor: pointer; }` (around line 864) and add immediately after it:

```css
    .eve-item { display: inline-flex; align-items: center; gap: 0.3rem; }
    .eve-item-icon {
      display: inline-flex; align-items: center; justify-content: center;
      width: 16px; height: 16px; padding: 0; border: 0; background: none;
      cursor: pointer; opacity: 0.55; transition: opacity 0.15s, filter 0.15s;
      vertical-align: middle; flex: 0 0 auto;
    }
    .eve-item-icon img { width: 14px; height: 14px; display: block; }
    .eve-item-icon:hover { opacity: 1; }
    .eve-item-icon.btn-ok  { opacity: 1; filter: hue-rotate(80deg) saturate(2); }
    .eve-item-icon.btn-fail { opacity: 1; filter: hue-rotate(-50deg) saturate(3); }
    .eve-item-icon:disabled { cursor: default; }
```

- [ ] **Step 2: Add the `itemName` helper and handlers**

Add a `<script>`-level function near the other render helpers. Place it directly above the existing `function openInEveBtn(contractId) {` definition (around line 2045):

```js
  // Shared renderer: an item/type name followed by in-game info + market icons.
  // Degrades to just the escaped name when no type id is available.
  function itemName(typeId, name, opts = {}) {
    const safe = escHtml(name == null ? '' : String(name));
    if (!typeId) return safe;
    const t = Number(typeId);
    const info = `<button class="eve-item-icon" title="Show info in EVE"
        aria-label="Show info in EVE"
        onclick="event.stopPropagation(); openItemInfo(this, ${t})">
        <img src="/info-icon.png" alt=""></button>`;
    const market = `<button class="eve-item-icon" title="View market in EVE"
        aria-label="View market in EVE"
        onclick="event.stopPropagation(); openItemMarket(this, ${t})">
        <img src="/market-icon.png" alt=""></button>`;
    return `<span class="eve-item"><span class="eve-item-name">${safe}</span>${info}${market}</span>`;
  }

  async function _openItemWindow(btn, url, okTitle) {
    btn.disabled = true;
    btn.classList.remove('btn-ok', 'btn-fail');
    try {
      const r = await fetch(url, { method: 'POST' });
      if (!r.ok) {
        const detail = await r.json().catch(() => ({}));
        btn.classList.add('btn-fail');
        btn.title = detail.detail || `Failed (${r.status})`;
      } else {
        btn.classList.add('btn-ok');
        btn.title = okTitle;
      }
    } catch {
      btn.classList.add('btn-fail');
      btn.title = 'Network error';
    }
    const baseTitle = btn.getAttribute('aria-label') || 'Open in EVE';
    setTimeout(() => {
      btn.classList.remove('btn-ok', 'btn-fail');
      btn.disabled = false;
      btn.title = baseTitle;
    }, 2500);
  }

  function openItemInfo(btn, typeId) {
    return _openItemWindow(btn, `/api/ui/open-information/${typeId}`, 'Sent to EVE client');
  }
  function openItemMarket(btn, typeId) {
    return _openItemWindow(btn, `/api/ui/open-market/${typeId}`, 'Sent to EVE client');
  }
```

- [ ] **Step 3: Verify there are no obvious syntax errors**

Run: `node --check static/index.html 2>/dev/null || echo "node check skipped (HTML file)"; grep -c "function itemName" static/index.html`
Expected: prints `1` for the grep count (the helper exists exactly once). The `node --check` on an HTML file is expected to be skipped — this is just a presence check.

- [ ] **Step 4: Commit**

```bash
git add static/index.html
git commit -m "feat: shared itemName() component with info/market icon buttons"
```

---

## Task 5: Wire all eight call sites to `itemName(...)`

**Files:**
- Modify: `static/index.html`

Each step replaces one render site. After each edit the name still shows; icons appear when a type id is present.

- [ ] **Step 1: Contract detail — items table (line ~2015)**

Replace:

```js
        <td style="color:#e2e8f0">${escHtml(i.name)}</td>
```

with:

```js
        <td style="color:#e2e8f0">${itemName(i.type_id, i.name)}</td>
```

- [ ] **Step 2: Contract detail — hero name**

The hero block (around line 1987) currently renders only an image and no text name. Locate the `meta` grid (around line 1996) and add a hero-name row as the first child of the grid, only when `hero.type_id` exists. Replace the opening of the `meta` template:

```js
    const meta = c ? `
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:0.5rem 1rem;
                  font-size:0.8rem;margin-bottom:1rem;padding-bottom:0.75rem;
                  border-bottom:1px solid #2d3348">
        <div><span style="color:#808fa6">Type</span><br>
```

with:

```js
    const heroItem = (d.items || []).find(i => i.type_id === hero.type_id);
    const heroName = hero.type_id
      ? `<div style="grid-column:1 / -1;color:#e2e8f0;font-weight:600">${itemName(hero.type_id, heroItem ? heroItem.name : ('Type #' + hero.type_id))}</div>`
      : '';
    const meta = c ? `
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:0.5rem 1rem;
                  font-size:0.8rem;margin-bottom:1rem;padding-bottom:0.75rem;
                  border-bottom:1px solid #2d3348">
        ${heroName}
        <div><span style="color:#808fa6">Type</span><br>
```

> The hero's type is always one of the contract items, so its resolved name is taken from `d.items` — no backend payload change needed. The `'Type #' + hero.type_id` fallback covers the rare case it isn't found.

- [ ] **Step 3: Contract list (ships tab) title cell (line ~1812)**

Replace:

```js
                <td style="color:#e2e8f0">
                  ${escHtml(c.title || 'Contract #' + c.contract_id)}
                  ${issuerLine(c)}
                </td>
```

with:

```js
                <td style="color:#e2e8f0">
                  ${itemName(c.icon_type_id, c.title || 'Contract #' + c.contract_id)}
                  ${issuerLine(c)}
                </td>
```

- [ ] **Step 4: Citadels table Type cell (line ~2227)**

Replace:

```js
                <td><span class="badge badge-blue">${escHtml(s.type_name || '–')}</span></td>
```

with:

```js
                <td><span class="badge badge-blue">${itemName(s.type_id, s.type_name || '–')}</span></td>
```

- [ ] **Step 5: POS table Type cell (line ~2248)**

Replace:

```js
                <td style="color:#e2e8f0">${escHtml(s.type_name || ('Type #' + (s.type_id || '')))}</td>
```

with:

```js
                <td style="color:#e2e8f0">${itemName(s.type_id, s.type_name || ('Type #' + (s.type_id || '')))}</td>
```

- [ ] **Step 6: POS fuel detail (line ~2332)**

Replace:

```js
         <span>${escHtml(f.type_name || 'Type #' + f.type_id)}</span>
```

with:

```js
         <span>${itemName(f.type_id, f.type_name || 'Type #' + f.type_id)}</span>
```

- [ ] **Step 7: Project card title (line ~2565)**

A project's title (`p.name`) is the item name for buy/sell projects; the backend resolves `p.market.type_id` from that name. Replace:

```js
          <div style="font-size:0.95rem;font-weight:600;color:#f8fafc">${escHtml(p.name)}</div>
```

with:

```js
          <div style="font-size:0.95rem;font-weight:600;color:#f8fafc">${itemName(p.market?.type_id, p.name)}</div>
```

> Projects whose name does not resolve to a market type (`p.market` is null) degrade to plain text — exactly the intended behavior.

- [ ] **Step 8: Member "Active Ship" name (line ~3872)**

Replace:

```js
            <div style="font-size:0.75rem;color:#94a3b8">${escHtml(ship?.type_name || '')}</div>
```

with:

```js
            <div style="font-size:0.75rem;color:#94a3b8">${itemName(ship?.type_id, ship?.type_name || '')}</div>
```

- [ ] **Step 9: Confirm no stray un-converted item-name sites remain**

Run: `grep -n "escHtml(i.name)\|escHtml(s.type_name\|escHtml(f.type_name\|escHtml(ship?.type_name" static/index.html`
Expected: no output (all converted).

- [ ] **Step 10: Commit**

```bash
git add static/index.html
git commit -m "feat: render item names via itemName() across contracts, structures, projects, member"
```

---

## Task 6: Manual smoke verification

**Files:** none (manual)

- [ ] **Step 1: Run the full test suite**

Run: `pytest -q`
Expected: all pass.

- [ ] **Step 2: Start the app and load it**

Start the app per the project's local-run method (see memory: `.env` sourced + system uvicorn), then in a browser:
- Open a contract detail → each item row shows name + two icons.
- Confirm clicking an icon does NOT also open/close the row (stopPropagation works).
- Open the structures (citadels/POS) tab → Type cells show icons.
- Open a member profile → Active Ship shows icons next to the ship type.
- Confirm a name with no type id (e.g. an unresolved entry) shows plain text, no broken image.

- [ ] **Step 3: Confirm icon assets serve**

Run: `curl -sI http://localhost:8000/info-icon.png | head -1 && curl -sI http://localhost:8000/market-icon.png | head -1`
Expected: both return `HTTP/1.1 200 OK` (adjust port to your local run).

---

## Notes / Out of Scope

- No backend payload shape changes — every site already has a `type_id` in hand.
- No event-delegation refactor; inline `onclick` matches the rest of the file.
- Frontend has no automated test harness, so Tasks 4–5 rely on the manual smoke test in Task 6.
