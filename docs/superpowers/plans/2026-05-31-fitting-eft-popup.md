# EVE Fitting Links → EFT Popup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make EVE in-game `fitting:` links in mail bodies clickable, opening a popup with the fit in EFT/Pyfa-importable text + a Copy button.

**Architecture:** Pure parser + ESI-backed type classifier + pure EFT formatter in `esi.py`; a session-gated `POST /api/me/fitting/eft` route in `main.py` that resolves the fit server-side (parallel ESI lookups, in-memory cache); a `sanitizeEveMail` branch + modal in `static/index.html`.

**Tech Stack:** FastAPI, httpx, plain HTML/CSS/JS, pytest + respx.

**Spec:** `docs/superpowers/specs/2026-05-31-fitting-eft-popup-design.md`

**Conventions (verified):**
- `ESI_BASE = "https://esi.evetech.net/latest"`, `import httpx` present in `esi.py`.
- `get_group_info(group_id) -> dict | None` already exists in `esi.py` and returns the full group JSON incl. `category_id`. Reuse it for drone detection.
- `get_type_info(type_id) -> {name, group_id}` exists (cached) — use it for the **ship** name. It does NOT return dogma effects, so a dedicated fetch is needed for module slot detection.
- Routes: `async def handler(body: dict, session: str | None = Cookie(None))` then `await get_current_character_id(session)`. `asyncio`, `Cookie`, `HTTPException`, `get_type_info`, `get_group_info` already imported in `main.py`.
- Frontend helpers: `showModal(title, html)`, `setModalBody(html)`, `closeModal()`, `escHtml`, `escAttr`; modal body element id `modal-body`. The mail body sanitizer is `sanitizeEveMail(raw)` (DOM-based, XSS-safe).
- Tests: `pytest` + `respx`; `client` fixture + `set_env` in `tests/conftest.py`. Run with `venv/bin/python -m pytest <path> -v` (NOT bare `pytest`).

---

## File Structure

- **Modify** `esi.py` — add `parse_fitting_link`, `get_fitting_type_meta` (+ `_fitting_meta_cache`, `SLOT_EFFECTS`, `DRONE_CATEGORY_ID`), `build_eft`.
- **Modify** `main.py` — import the three new esi functions; add `POST /api/me/fitting/eft`.
- **Modify** `static/index.html` — `sanitizeEveMail` fitting branch; `openFittingModal`/`renderFittingModal`/`copyFitEft`; CSS.
- **Create** `tests/test_fitting.py` — parser, classifier, formatter, route tests.

---

## Task 1: `parse_fitting_link` (pure parser)

**Files:**
- Modify: `esi.py` (append near the other universe helpers, after `get_group_info`)
- Test: `tests/test_fitting.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_fitting.py`:

```python
from esi import parse_fitting_link


def test_parse_fitting_basic():
    href = "fitting:621:31360;1:1877;5:27361;1000::"
    ship_id, items = parse_fitting_link(href)
    assert ship_id == 621
    assert items == [(31360, 1), (1877, 5), (27361, 1000)]


def test_parse_fitting_defaults_qty_when_missing():
    ship_id, items = parse_fitting_link("fitting:621:31360::")
    assert ship_id == 621
    assert items == [(31360, 1)]


def test_parse_fitting_rejects_non_fitting():
    assert parse_fitting_link("showinfo:1377//94409715") == (0, [])
    assert parse_fitting_link("fitting:abc:1;1::") == (0, [])
```

- [ ] **Step 2: Run to verify it fails**

Run: `venv/bin/python -m pytest tests/test_fitting.py -v`
Expected: FAIL (ImportError: cannot import name 'parse_fitting_link').

- [ ] **Step 3: Implement**

Append to `esi.py`:

```python
def parse_fitting_link(href: str) -> tuple[int, list[tuple[int, int]]]:
    """Parse an EVE 'fitting:<shipID>:<typeID>;<qty>:...::' link.
    Returns (ship_type_id, [(type_id, qty), ...]); (0, []) if not a fitting link."""
    if not href.startswith("fitting:"):
        return (0, [])
    parts = href[len("fitting:"):].split(":")
    if not parts or not parts[0].isdigit():
        return (0, [])
    ship_id = int(parts[0])
    items: list[tuple[int, int]] = []
    for field in parts[1:]:
        if not field:
            continue
        tid, _, qty = field.partition(";")
        if tid.isdigit():
            items.append((int(tid), int(qty) if qty.isdigit() else 1))
    return (ship_id, items)
```

- [ ] **Step 4: Run to verify it passes**

Run: `venv/bin/python -m pytest tests/test_fitting.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add esi.py tests/test_fitting.py
git commit -m "feat: parse EVE fitting links"
```

---

## Task 2: `get_fitting_type_meta` (slot/kind classifier)

**Files:**
- Modify: `esi.py` (append after `parse_fitting_link`)
- Test: `tests/test_fitting.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_fitting.py` (add imports at top: `import respx`, `from httpx import Response`, `from esi import get_fitting_type_meta, _fitting_meta_cache`):

```python
T = "https://esi.evetech.net/latest/universe/types"
G = "https://esi.evetech.net/latest/universe/groups"


@respx.mock
async def test_meta_low_module():
    _fitting_meta_cache.clear()
    respx.get(f"{T}/100/").mock(return_value=Response(200, json={
        "name": "Damage Control II", "group_id": 60,
        "dogma_effects": [{"effect_id": 11, "is_default": True}]}))
    m = await get_fitting_type_meta(100)
    assert m == {"name": "Damage Control II", "slot": "low", "kind": "module"}


@respx.mock
async def test_meta_high_module():
    _fitting_meta_cache.clear()
    respx.get(f"{T}/101/").mock(return_value=Response(200, json={
        "name": "Heavy Missile Launcher II", "group_id": 510,
        "dogma_effects": [{"effect_id": 12}]}))
    m = await get_fitting_type_meta(101)
    assert m["slot"] == "high" and m["kind"] == "module"


@respx.mock
async def test_meta_rig_module():
    _fitting_meta_cache.clear()
    respx.get(f"{T}/102/").mock(return_value=Response(200, json={
        "name": "Medium Core Defense Field Extender I", "group_id": 781,
        "dogma_effects": [{"effect_id": 2663}]}))
    m = await get_fitting_type_meta(102)
    assert m["slot"] == "rig" and m["kind"] == "module"


@respx.mock
async def test_meta_drone():
    _fitting_meta_cache.clear()
    respx.get(f"{T}/103/").mock(return_value=Response(200, json={
        "name": "Hobgoblin II", "group_id": 100, "dogma_effects": []}))
    respx.get(f"{G}/100/").mock(return_value=Response(200, json={
        "group_id": 100, "name": "Light Scout Drone", "category_id": 18}))
    m = await get_fitting_type_meta(103)
    assert m == {"name": "Hobgoblin II", "slot": None, "kind": "drone"}


@respx.mock
async def test_meta_charge_and_cache():
    _fitting_meta_cache.clear()
    route = respx.get(f"{T}/104/").mock(return_value=Response(200, json={
        "name": "Scourge Heavy Missile", "group_id": 384, "dogma_effects": [{"effect_id": 9}]}))
    respx.get(f"{G}/384/").mock(return_value=Response(200, json={
        "group_id": 384, "name": "Heavy Missile", "category_id": 8}))
    m1 = await get_fitting_type_meta(104)
    m2 = await get_fitting_type_meta(104)   # served from cache
    assert m1 == {"name": "Scourge Heavy Missile", "slot": None, "kind": "charge"}
    assert m2 == m1
    assert route.call_count == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `venv/bin/python -m pytest tests/test_fitting.py -v`
Expected: FAIL (ImportError for `get_fitting_type_meta` / `_fitting_meta_cache`).

- [ ] **Step 3: Implement**

Append to `esi.py`:

```python
# Module slot dogma effects → EFT slot name.
SLOT_EFFECTS = {11: "low", 13: "mid", 12: "high", 2663: "rig", 3772: "subsystem"}
DRONE_CATEGORY_ID = 18

# Static per-type metadata; cached in-memory for the process.
_fitting_meta_cache: dict[int, dict] = {}


async def get_fitting_type_meta(type_id: int) -> dict:
    """Returns {name, slot, kind} for a fitting item.
    slot ∈ low/mid/high/rig/subsystem/None; kind ∈ module/drone/charge."""
    if type_id in _fitting_meta_cache:
        return _fitting_meta_cache[type_id]
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(f"{ESI_BASE}/universe/types/{type_id}/")
            r.raise_for_status()
            data = r.json()
        except httpx.HTTPError:
            return {"name": f"Type #{type_id}", "slot": None, "kind": "charge"}
    name = data.get("name", f"Type #{type_id}")
    slot = None
    for eff in data.get("dogma_effects", []):
        s = SLOT_EFFECTS.get(eff.get("effect_id"))
        if s:
            slot = s
            break
    if slot:
        meta = {"name": name, "slot": slot, "kind": "module"}
    else:
        group_id = data.get("group_id")
        group = await get_group_info(group_id) if group_id else None
        kind = "drone" if group and group.get("category_id") == DRONE_CATEGORY_ID else "charge"
        meta = {"name": name, "slot": None, "kind": kind}
    _fitting_meta_cache[type_id] = meta
    return meta
```

- [ ] **Step 4: Run to verify it passes**

Run: `venv/bin/python -m pytest tests/test_fitting.py -v`
Expected: PASS (8 passed).

- [ ] **Step 5: Commit**

```bash
git add esi.py tests/test_fitting.py
git commit -m "feat: classify fitting item slot/kind via ESI dogma data"
```

---

## Task 3: `build_eft` (pure formatter)

**Files:**
- Modify: `esi.py` (append after `get_fitting_type_meta`)
- Test: `tests/test_fitting.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_fitting.py` (add `from esi import build_eft` to the imports):

```python
def test_build_eft_groups_and_formats():
    items = [
        {"name": "Damage Control II", "slot": "low", "kind": "module", "qty": 1},
        {"name": "Large Shield Extender II", "slot": "mid", "kind": "module", "qty": 1},
        {"name": "Heavy Missile Launcher II", "slot": "high", "kind": "module", "qty": 3},
        {"name": "Medium Core Defense Field Extender I", "slot": "rig", "kind": "module", "qty": 1},
        {"name": "Hobgoblin II", "slot": None, "kind": "drone", "qty": 2},
        {"name": "Scourge Heavy Missile", "slot": None, "kind": "charge", "qty": 1000},
    ]
    eft = build_eft("Caracal", "test", items)
    assert eft == (
        "[Caracal, test]\n"
        "Damage Control II\n"
        "\n"
        "Large Shield Extender II\n"
        "\n"
        "Heavy Missile Launcher II\n"
        "Heavy Missile Launcher II\n"
        "Heavy Missile Launcher II\n"
        "\n"
        "Medium Core Defense Field Extender I\n"
        "\n"
        "Hobgoblin II x2\n"
        "\n"
        "Scourge Heavy Missile x1000"
    )
```

- [ ] **Step 2: Run to verify it fails**

Run: `venv/bin/python -m pytest tests/test_fitting.py::test_build_eft_groups_and_formats -v`
Expected: FAIL (ImportError: cannot import name 'build_eft').

- [ ] **Step 3: Implement**

Append to `esi.py`:

```python
def build_eft(ship_name: str, fit_name: str, items: list[dict]) -> str:
    """Render items ([{name, slot, kind, qty}]) as EFT/Pyfa-importable text.
    Fitted modules expand to one line per unit; drones/cargo use 'Name xqty'.
    Non-empty slot sections (low, mid, high, rig, subsystem) then drones then cargo,
    each separated by a blank line."""
    order = ["low", "mid", "high", "rig", "subsystem"]
    slots: dict[str, list[str]] = {k: [] for k in order}
    drones: list[str] = []
    cargo: list[str] = []
    for it in items:
        qty = it.get("qty", 1)
        kind = it.get("kind")
        if kind == "module" and it.get("slot") in slots:
            slots[it["slot"]] += [it["name"]] * qty
        elif kind == "drone":
            drones.append(f"{it['name']} x{qty}")
        else:
            cargo.append(f"{it['name']} x{qty}")
    sections = [
        "\n".join(slots[k]) for k in order if slots[k]
    ]
    if drones:
        sections.append("\n".join(drones))
    if cargo:
        sections.append("\n".join(cargo))
    body = "\n\n".join(sections)
    header = f"[{ship_name}, {fit_name}]"
    return f"{header}\n{body}" if body else header
```

- [ ] **Step 4: Run to verify it passes**

Run: `venv/bin/python -m pytest tests/test_fitting.py -v`
Expected: PASS (9 passed).

- [ ] **Step 5: Commit**

```bash
git add esi.py tests/test_fitting.py
git commit -m "feat: build EFT text from resolved fitting items"
```

---

## Task 4: `POST /api/me/fitting/eft` route

**Files:**
- Modify: `main.py` — extend the `from esi import (...)` block; add the route in the Mail area.
- Test: `tests/test_fitting.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_fitting.py` (add imports: `import time`, `from db import upsert_token`, `from auth import make_session_cookie`):

```python
TYPES = "https://esi.evetech.net/latest/universe/types"
GROUPS = "https://esi.evetech.net/latest/universe/groups"


async def _fit_auth(character_id=777):
    await upsert_token(character_id=character_id, character_name="Pilot",
                       access_token="a", refresh_token="r",
                       expires_at=time.time() + 1200, corporation_id=98340844)
    return {"session": make_session_cookie(character_id)}


async def test_fitting_eft_requires_auth(client):
    r = await client.post("/api/me/fitting/eft", json={"fitting": "fitting:621:100;1::", "name": "x"})
    assert r.status_code == 401


async def test_fitting_eft_rejects_non_fitting(client):
    cookies = await _fit_auth()
    r = await client.post("/api/me/fitting/eft",
                          json={"fitting": "showinfo:1377//1", "name": "x"}, cookies=cookies)
    assert r.status_code == 400


@respx.mock
async def test_fitting_eft_happy_path(client):
    _fitting_meta_cache.clear()
    cookies = await _fit_auth()
    # ship 621 -> Caracal (via get_type_info, public types endpoint)
    respx.get(f"{TYPES}/621/").mock(return_value=Response(200, json={
        "name": "Caracal", "group_id": 26}))
    respx.get(f"{TYPES}/100/").mock(return_value=Response(200, json={
        "name": "Damage Control II", "group_id": 60,
        "dogma_effects": [{"effect_id": 11}]}))
    respx.get(f"{TYPES}/200/").mock(return_value=Response(200, json={
        "name": "Hobgoblin II", "group_id": 100, "dogma_effects": []}))
    respx.get(f"{GROUPS}/100/").mock(return_value=Response(200, json={
        "group_id": 100, "name": "Light Scout Drone", "category_id": 18}))
    r = await client.post("/api/me/fitting/eft", cookies=cookies,
                          json={"fitting": "fitting:621:100;1:200;2::", "name": "ess"})
    assert r.status_code == 200
    data = r.json()
    assert data["ship"] == "Caracal"
    assert data["eft"] == (
        "[Caracal, ess]\n"
        "Damage Control II\n"
        "\n"
        "Hobgoblin II x2"
    )
```

> Note: `get_type_info` caches the ship type in the SQLite type cache; the `client` fixture uses a fresh temp DB per test (`set_env`), so 621 is fetched from the mocked ESI. `_fitting_meta_cache` is process-global, so the test clears it first.

- [ ] **Step 2: Run to verify it fails**

Run: `venv/bin/python -m pytest tests/test_fitting.py -k fitting_eft -v`
Expected: FAIL (404 / route undefined).

- [ ] **Step 3: Implement**

In `main.py`, extend the `from esi import (` block (around lines 19-34) to also import:

```python
    parse_fitting_link, get_fitting_type_meta, build_eft,
```

Then add this route alongside the other `/api/me/*` routes (e.g. just after the mail routes section). The path prefix `/api/me/fitting/` is distinct, so there is no `{mail_id}` ordering concern:

```python
@app.post("/api/me/fitting/eft")
async def my_fitting_eft(body: dict, session: str | None = Cookie(None)):
    await get_current_character_id(session)
    fitting = body.get("fitting") or ""
    fit_name = (body.get("name") or "").strip() or "Fitting"
    ship_id, pairs = parse_fitting_link(fitting)
    if not ship_id:
        raise HTTPException(status_code=400, detail="Not a fitting link")
    try:
        ship_info, *metas = await asyncio.gather(
            get_type_info(ship_id),
            *[get_fitting_type_meta(tid) for tid, _ in pairs],
        )
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="Could not load fitting (ESI unavailable)")
    items = [{**m, "qty": qty} for m, (tid, qty) in zip(metas, pairs)]
    ship_name = ship_info.get("name", f"Type #{ship_id}")
    return {"eft": build_eft(ship_name, fit_name, items), "ship": ship_name}
```

- [ ] **Step 4: Run to verify it passes**

Run: `venv/bin/python -m pytest tests/test_fitting.py -v`
Expected: PASS (all green).

- [ ] **Step 5: Run the full suite, then commit**

Run: `venv/bin/python -m pytest -q` (expect all green)

```bash
git add main.py tests/test_fitting.py
git commit -m "feat: POST /api/me/fitting/eft resolves a fitting link to EFT text"
```

---

## Task 5: Frontend — clickable fitting link + EFT modal

**Files:**
- Modify: `static/index.html` — `sanitizeEveMail` branch, modal JS, CSS.
- Verification: manual (no JS test harness).

- [ ] **Step 1: Add the fitting branch in `sanitizeEveMail`**

Find the `if (tag === 'A') {` block. It currently handles `http(s)`, then `killReport:` (zKillboard), then an `else` for client-only links. Insert a `fitting:` branch BEFORE the final `else`. The relevant block becomes:

```javascript
        if (tag === 'A') {
          const href = node.getAttribute('href') || '';
          // killReport:<killmail_id>:<hash> → public zKillboard page.
          const kill = href.match(/^killReport:(\d+):/i);
          if (/^https?:\/\//i.test(href)) {
            const a = document.createElement('a');
            a.href = href; a.target = '_blank'; a.rel = 'noopener noreferrer';
            a.style.color = '#38bdf8';
            walk(node, a); dst.appendChild(a);
          } else if (kill) {
            const a = document.createElement('a');
            a.href = 'https://zkillboard.com/kill/' + kill[1] + '/';
            a.target = '_blank'; a.rel = 'noopener noreferrer';
            a.style.color = '#38bdf8';
            walk(node, a); dst.appendChild(a);
          } else if (/^fitting:/i.test(href)) {
            const a = document.createElement('a');
            a.href = '#'; a.className = 'mail-fitting-link'; a.style.color = '#38bdf8';
            const fitName = node.textContent || 'Fitting';
            a.onclick = (e) => { e.preventDefault(); openFittingModal(href, fitName); };
            walk(node, a); dst.appendChild(a);
          } else {
            // showinfo:/etc — client-only, render as plain styled text
            const span = document.createElement('span');
            span.className = 'mail-link-disabled';
            walk(node, span); dst.appendChild(span);
          }
          return;
        }
```

(Only the `else if (/^fitting:/i...)` block is new; keep the rest exactly as-is.)

- [ ] **Step 2: Add the modal JS**

Add near the other mail JS functions (e.g. right after `renderMailReader`):

```javascript
  async function openFittingModal(fittingStr, fitName) {
    showModal('Fitting', '<div style="color:#66768c;text-align:center;padding:1.5rem">Loading fit…</div>');
    try {
      const r = await fetch('/api/me/fitting/eft', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ fitting: fittingStr, name: fitName }) });
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        setModalBody('<div class="banner show">' + escHtml(d.detail || 'Could not load fitting.') + '</div>');
        return;
      }
      const data = await r.json();
      renderFittingModal(data.eft);
    } catch {
      setModalBody('<div class="banner show">Network error loading fitting.</div>');
    }
  }

  function renderFittingModal(eft) {
    const body = document.getElementById('modal-body');
    body.innerHTML = '';
    const bar = document.createElement('div');
    bar.style.cssText = 'display:flex;justify-content:flex-end;margin-bottom:0.5rem';
    const btn = document.createElement('button');
    btn.className = 'btn-action'; btn.textContent = 'Copy';
    btn.onclick = () => copyFitEft(eft, btn);
    bar.appendChild(btn);
    const pre = document.createElement('pre');
    pre.className = 'fit-eft';
    pre.textContent = eft;             // textContent → XSS-safe
    body.appendChild(bar);
    body.appendChild(pre);
  }

  async function copyFitEft(text, btn) {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const ta = document.createElement('textarea');
      ta.value = text; document.body.appendChild(ta); ta.select();
      try { document.execCommand('copy'); } catch {}
      document.body.removeChild(ta);
    }
    btn.textContent = 'Copied!';
    setTimeout(() => { btn.textContent = 'Copy'; }, 1500);
  }
```

- [ ] **Step 3: Add CSS** (near the mail CSS block):

```css
    .mail-fitting-link { text-decoration: underline; cursor: pointer; }
    .fit-eft { background: #0f1117; border: 1px solid #2d3348; border-radius: 8px;
      padding: 0.75rem; margin: 0; color: #cbd5e1; font-family: ui-monospace, SFMono-Regular,
      Menlo, monospace; font-size: 0.8rem; line-height: 1.45; white-space: pre;
      overflow-x: auto; max-height: 60vh; overflow-y: auto; }
```

- [ ] **Step 4: Verify**

- Confirm wiring: `grep -n "mail-fitting-link\|openFittingModal\|renderFittingModal\|copyFitEft\|fit-eft" static/index.html`
- Confirm exactly one definition each: `grep -c "function openFittingModal" static/index.html` (expect 1).
- Smoke check the server starts: `set -a && source .env && set +a && timeout 5 /home/paolo/.local/bin/uvicorn main:app --port 8021` (expect "Application startup complete"; timeout is fine). If env/uvicorn unavailable, run `venv/bin/python -m pytest -q` and note browser verification deferred.
- Reason through: a mail with `<a href="fitting:621:100;1::">ess</a>` → renders an underlined accent link; clicking calls `openFittingModal("fitting:621:100;1::", "ess")` → POST → modal shows the EFT text + Copy. The body is set via `textContent` (no untrusted innerHTML).

- [ ] **Step 5: Commit**

```bash
git add static/index.html
git commit -m "feat: clickable fitting links open an EFT popup with copy"
```

---

## Final verification

- [ ] **Full backend suite**

Run: `venv/bin/python -m pytest -q`
Expected: all green (existing + new `tests/test_fitting.py`).

- [ ] **Manual smoke** — open the Mail page, open a mail containing a `fitting:` link, click it, confirm the EFT text matches the fit and Copy works. (Requires being logged in.)

---

## Self-Review Notes (author)

- **Spec coverage:** parser (T1), slot/kind classifier incl. drone-via-category and degrade-to-cargo (T2), EFT formatter with module expansion + `xqty` (T3), session-gated route with parallel resolve + 400/502 (T4), sanitizer branch + modal + Copy + CSS (T5), tests in each backend task. All spec sections mapped.
- **Whitespace note:** `build_eft` joins only non-empty slot sections with single blank lines (cleaner than, and import-equivalent to, the example's empty-subsystem double blank — the user accepted grouping-over-exact-order). The golden test pins the exact output.
- **Type consistency:** meta dict shape `{name, slot, kind}` is identical in T2, T3, T4; route attaches `qty` to form `{name, slot, kind, qty}` consumed by `build_eft`. `_fitting_meta_cache` cleared in tests (process-global). `parse_fitting_link` returns `(int, list[tuple[int,int]])` consumed consistently in T4.
- **No JS test harness** — T5 uses manual verification + server smoke, consistent with the codebase.
