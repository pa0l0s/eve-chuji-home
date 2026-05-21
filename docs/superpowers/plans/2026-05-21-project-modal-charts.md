# Project Modal Charts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** In the project detail modal, replace the ranked contributor list with a donut pie chart + legend, and add a 90-day Jita price-history line chart for projects that have a reward + resolved item type.

**Architecture:** A new ESI helper fetches market history; a new session-protected, in-memory-cached FastAPI route exposes the last 90 days; the frontend renders two hand-rolled inline-SVG charts (matching the existing `renderSkillRadar` style).

**Tech Stack:** FastAPI (Python), `httpx`, hand-rolled SVG in `static/index.html`, `pytest` + `respx` + `httpx.AsyncClient`.

---

## Preconditions

- Branch: work on `master` (the project's working branch); commit there.
- `respx` and `pytest` are already available (used by `tests/test_contract_detail.py`).
- To run the app locally for verification: the venv has no `uvicorn` and `main.py` needs env vars, so use:
  `set -a && source .env && set +a && ~/.local/bin/uvicorn main:app --port <PORT>`

## File Structure

- **Modify** `esi.py` — add `get_market_history(type_id)` (one ESI call, best-effort).
- **Modify** `main.py` — add `get_market_history` to the `esi` import; add `_market_history_cache` + `_cached_market_history` + the `GET /api/market/{type_id}/history` route.
- **Create** `tests/test_market_history.py` — tests for the ESI helper and the route.
- **Modify** `static/index.html` — add `renderContributorPie`, rewire `renderContributors`; add `renderPriceHistoryChart` + `loadPriceHistory`, rewire `openProjectContributors`.

---

## Task 1: `get_market_history` in `esi.py`

**Files:**
- Modify: `esi.py` (add a function after `get_jita_prices`)
- Test: `tests/test_market_history.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_market_history.py` with exactly this content:

```python
import respx
from httpx import Response

from esi import get_market_history

HISTORY_URL = "https://esi.evetech.net/latest/markets/10000002/history/"


@respx.mock
async def test_market_history_returns_records_sorted_by_date():
    respx.get(HISTORY_URL).mock(return_value=Response(200, json=[
        {"date": "2026-03-02", "average": 5.2, "highest": 5.5, "lowest": 5.0},
        {"date": "2026-03-01", "average": 5.0, "highest": 5.3, "lowest": 4.8},
    ]))
    out = await get_market_history(34)
    assert [d["date"] for d in out] == ["2026-03-01", "2026-03-02"]


@respx.mock
async def test_market_history_returns_empty_on_http_error():
    respx.get(HISTORY_URL).mock(return_value=Response(404))
    assert await get_market_history(999999) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_market_history.py -v`
Expected: FAIL — `ImportError: cannot import name 'get_market_history' from 'esi'`.

- [ ] **Step 3: Implement `get_market_history`**

In `esi.py`, immediately after the `get_jita_prices` function (it ends near line 580, just before `get_corp_structures`), add:

```python
async def get_market_history(type_id: int) -> list[dict]:
    """Daily Jita market history (The Forge) for the type, oldest first.
    Each record: {date, average, highest, lowest, order_count, volume}.
    Returns [] on any ESI failure (incl. a 404 for an untraded type)."""
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(
                f"{ESI_BASE}/markets/{FORGE_REGION_ID}/history/",
                params={"type_id": type_id},
            )
            r.raise_for_status()
            return sorted(r.json(), key=lambda d: d.get("date", ""))
        except httpx.HTTPError:
            return []
```

(`httpx`, `ESI_BASE`, and `FORGE_REGION_ID` are already defined at the top of `esi.py`.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_market_history.py -v`
Expected: PASS — 2 passed.

- [ ] **Step 5: Commit**

```bash
git add esi.py tests/test_market_history.py
git commit -m "$(cat <<'EOF'
feat: get_market_history — ESI Jita market history helper

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: `GET /api/market/{type_id}/history` endpoint

**Files:**
- Modify: `main.py` (esi import block near line 26; new route + cache after the `/api/projects/{project_id}/contributors` route, which ends at line 822)
- Test: `tests/test_market_history.py` (append)

- [ ] **Step 1: Write the failing tests**

Append exactly this to `tests/test_market_history.py`:

```python
import time as _time

import pytest
from db import init_db, upsert_token
from auth import make_session_cookie


@pytest.fixture(autouse=True)
async def _setup_db():
    await init_db()


async def _auth_cookie(character_id: int = 123) -> dict:
    await upsert_token(
        character_id=character_id,
        character_name="Test Pilot",
        access_token="valid_acc",
        refresh_token="valid_ref",
        expires_at=_time.time() + 1200,
        corporation_id=98340844,
    )
    return {"session": make_session_cookie(character_id)}


async def test_market_history_endpoint_requires_session(client):
    r = await client.get("/api/market/34/history")
    assert r.status_code == 401


@respx.mock
async def test_market_history_endpoint_returns_last_90(client):
    from datetime import date, timedelta
    base = date(2026, 1, 1)
    rows = [{"date": str(base + timedelta(days=i)), "average": float(i),
             "highest": float(i) + 1.0, "lowest": max(0.0, float(i) - 1.0)}
            for i in range(100)]
    respx.get(HISTORY_URL).mock(return_value=Response(200, json=rows))
    cookies = await _auth_cookie()
    r = await client.get("/api/market/34/history", cookies=cookies)
    assert r.status_code == 200
    data = r.json()
    assert data["type_id"] == 34
    assert len(data["history"]) == 90
    assert data["history"][-1]["date"] == str(base + timedelta(days=99))


@respx.mock
async def test_market_history_endpoint_caches(client):
    route = respx.get(HISTORY_URL).mock(return_value=Response(200, json=[
        {"date": "2026-03-01", "average": 5.0, "highest": 5.3, "lowest": 4.8},
        {"date": "2026-03-02", "average": 5.2, "highest": 5.5, "lowest": 5.0},
    ]))
    cookies = await _auth_cookie()
    await client.get("/api/market/77/history", cookies=cookies)
    await client.get("/api/market/77/history", cookies=cookies)
    assert route.call_count == 1
```

The `client` fixture (from `tests/conftest.py`) reloads `main` per test, so `_market_history_cache` starts empty each test.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_market_history.py -v`
Expected: the 3 new tests FAIL — the route does not exist, so `/api/market/...` hits the SPA catch-all (the requires-session test gets 200 HTML instead of 401; the others fail on status/JSON).

- [ ] **Step 3: Add `get_market_history` to the esi import in `main.py`**

In `main.py`, in the `from esi import (...)` block, change line 26 from:

```python
    get_jita_prices,
```

to:

```python
    get_jita_prices, get_market_history,
```

- [ ] **Step 4: Add the cache helper and route in `main.py`**

In `main.py`, immediately after the `/api/projects/{project_id}/contributors` route handler (it ends at line 822 with `return {"project_id": project_id, "contributors": contributors}`), add:

```python


# Jita market history updates ~once/day; cache per type for 6 h to bound ESI load.
_market_history_cache: dict[int, tuple[float, list]] = {}
MARKET_HISTORY_TTL = 6 * 3600


async def _cached_market_history(type_id: int) -> list:
    cached = _market_history_cache.get(type_id)
    if cached and (time.time() - cached[0]) < MARKET_HISTORY_TTL:
        return cached[1]
    rows = await get_market_history(type_id)
    _market_history_cache[type_id] = (time.time(), rows)
    return rows


@app.get("/api/market/{type_id}/history")
async def market_history(type_id: int, session: str | None = Cookie(None)):
    """Last 90 days of Jita market history for a type. Session-protected."""
    await get_current_character_id(session)  # raises 401 without a valid session
    rows = await _cached_market_history(type_id)
    return {"type_id": type_id, "history": rows[-90:]}
```

(`time`, `Cookie`, `app`, and `get_current_character_id` are all already in scope in `main.py`.)

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_market_history.py -v`
Expected: PASS — 5 passed.

- [ ] **Step 6: Run the full test suite**

Run: `pytest -q`
Expected: all tests pass, no regressions.

- [ ] **Step 7: Commit**

```bash
git add main.py tests/test_market_history.py
git commit -m "$(cat <<'EOF'
feat: add /api/market/{type_id}/history endpoint with 6h cache

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Contributor pie chart (frontend)

**Files:**
- Modify: `static/index.html` — replace the row-list body of `renderContributors` (lines 2021–2047) and add `renderContributorPie` after it.

No automated test — `static/index.html` is plain HTML/JS with no test harness. Verified by sanity checks in Step 3.

- [ ] **Step 1: Replace the list body of `renderContributors`**

In `static/index.html`, find this block at the end of `renderContributors` (lines 2021–2047):

```javascript
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

    return header + rows;
  }
```

Replace that entire block with:

```javascript
    return header + renderContributorPie(sorted, totalContributed);
  }

  const PIE_COLORS = ['#38bdf8', '#4ade80', '#fbbf24', '#f87171', '#a78bfa',
                      '#fb923c', '#2dd4bf', '#f472b6', '#60a5fa', '#a3e635'];
  const PIE_OTHERS_COLOR = '#475569';

  // Donut pie of contributor share + compact legend. `sorted` is contributors
  // sorted by contributed desc; `total` is the sum of all contributed.
  function renderContributorPie(sorted, total) {
    if (total <= 0) {
      return '<p style="color:#475569;text-align:center;padding:1rem">No contributions recorded.</p>';
    }
    // Top 10 contributors as slices; rank 11+ collapse into one "Others" slice.
    const slices = sorted.slice(0, 10).map((c, i) => ({
      label: c.name || 'Unknown', value: c.contributed || 0, color: PIE_COLORS[i],
    }));
    if (sorted.length > 10) {
      const rest = sorted.slice(10).reduce((s, c) => s + (c.contributed || 0), 0);
      if (rest > 0) slices.push({ label: 'Others', value: rest, color: PIE_OTHERS_COLOR });
    }

    const size = 180, r = 70, sw = 34, cx = size / 2, cy = size / 2;
    const circ = 2 * Math.PI * r;
    let acc = 0;
    const ring = slices.map(s => {
      const frac = s.value / total;
      const seg = frac * circ;
      const tip = `${s.label}: ${fmtNum(s.value)} (${(frac * 100).toFixed(1)}%)`;
      const c = `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none"
          stroke="${s.color}" stroke-width="${sw}"
          stroke-dasharray="${seg.toFixed(2)} ${(circ - seg).toFixed(2)}"
          stroke-dashoffset="${(-acc).toFixed(2)}"><title>${escAttr(tip)}</title></circle>`;
      acc += seg;
      return c;
    }).join('');

    const legend = slices.map(s => {
      const pct = (s.value / total * 100).toFixed(1);
      return `
        <div style="display:flex;align-items:center;gap:0.5rem;font-size:0.8rem;padding:0.15rem 0">
          <span style="width:10px;height:10px;border-radius:2px;background:${s.color};flex:0 0 auto"></span>
          <span style="color:#e2e8f0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1 1 auto">${escHtml(s.label)}</span>
          <span style="color:#64748b;font-variant-numeric:tabular-nums;flex:0 0 auto">${pct}%</span>
        </div>`;
    }).join('');

    return `
      <div style="display:flex;gap:1.25rem;align-items:center;flex-wrap:wrap;justify-content:center">
        <svg viewBox="0 0 ${size} ${size}" width="${size}" height="${size}" style="flex:0 0 auto;display:block">
          <g transform="rotate(-90 ${cx} ${cy})">${ring}</g>
        </svg>
        <div style="flex:1 1 200px;min-width:180px">${legend}</div>
      </div>`;
  }
```

This keeps the existing `header` (progress bar, contributor count, reward pool) untouched and computes nothing new — `sorted` and `totalContributed` already exist earlier in `renderContributors`. `escHtml`, `escAttr`, and `fmtNum` are existing helpers.

- [ ] **Step 2: Sanity-check the page serves**

Run:
```bash
set -a && source .env && set +a && ~/.local/bin/uvicorn main:app --port 8021 >/tmp/uv3.log 2>&1 &
sleep 4
curl -s -o /dev/null -w "page=%{http_code}\n" http://localhost:8021/
curl -s http://localhost:8021/ | grep -c "renderContributorPie"
```
Expected: `page=200`, then `2` (the function definition + its call site).

- [ ] **Step 3: Stop the server**

Run: `pkill -f "uvicorn main:app --port 8021"`

- [ ] **Step 4: Commit**

```bash
git add static/index.html
git commit -m "$(cat <<'EOF'
feat: contributor donut pie chart replaces the ranked list

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Jita price history chart (frontend)

**Files:**
- Modify: `static/index.html` — add `renderPriceHistoryChart` + `loadPriceHistory`, and rewire `openProjectContributors` (lines 1974–1993).

No automated test — verified by sanity checks in Step 3.

- [ ] **Step 1: Add `renderPriceHistoryChart` and `loadPriceHistory`**

In `static/index.html`, immediately after the closing `}` of the `renderContributorPie` function added in Task 3, add:

```javascript

  // SVG line chart of the last 90 days of Jita price. `history` is ascending
  // daily records {date, average, highest, lowest}; `projectPrice` (ISK/unit)
  // is drawn as a dashed reference line, or null to omit it.
  function renderPriceHistoryChart(history, projectPrice) {
    const open = `<div style="margin-top:1rem;padding-top:0.75rem;border-top:1px solid #2d3348">
      <div style="font-size:0.7rem;font-weight:600;color:#64748b;text-transform:uppercase;
                  letter-spacing:0.08em;margin-bottom:0.5rem">Jita price &middot; last 90 days</div>`;
    if (!Array.isArray(history) || history.length < 2) {
      return open + '<div style="color:#475569;font-size:0.75rem;text-align:center;padding:0.5rem">Price history unavailable</div></div>';
    }

    const W = 460, H = 170, padL = 64, padR = 12, padT = 12, padB = 26;
    const plotW = W - padL - padR, plotH = H - padT - padB;
    const highs = history.map(d => d.highest);
    const lows = history.map(d => d.lowest);
    let lo = Math.min(...lows), hi = Math.max(...highs);
    if (projectPrice) { lo = Math.min(lo, projectPrice); hi = Math.max(hi, projectPrice); }
    if (hi === lo) hi = lo + 1;
    const margin = (hi - lo) * 0.08;
    lo -= margin; hi += margin;

    const n = history.length;
    const xAt = i => padL + i / (n - 1) * plotW;
    const yAt = v => padT + (1 - (v - lo) / (hi - lo)) * plotH;

    const bandTop = history.map((d, i) => `${xAt(i).toFixed(1)},${yAt(d.highest).toFixed(1)}`);
    const bandBot = history.map((d, i) => `${xAt(i).toFixed(1)},${yAt(d.lowest).toFixed(1)}`).reverse();
    const band = `<polygon points="${bandTop.concat(bandBot).join(' ')}" fill="rgba(56,189,248,0.12)"/>`;

    const linePts = history.map((d, i) => `${xAt(i).toFixed(1)},${yAt(d.average).toFixed(1)}`).join(' ');
    const line = `<polyline points="${linePts}" fill="none" stroke="#38bdf8" stroke-width="2"
                   stroke-linejoin="round" stroke-linecap="round"/>`;

    let refLine = '';
    if (projectPrice) {
      const ry = yAt(projectPrice);
      refLine = `
        <line x1="${padL}" y1="${ry.toFixed(1)}" x2="${(padL + plotW).toFixed(1)}" y2="${ry.toFixed(1)}"
              stroke="#fbbf24" stroke-width="1.5" stroke-dasharray="5 4"/>
        <text x="${(padL + plotW).toFixed(1)}" y="${(ry - 4).toFixed(1)}" text-anchor="end"
              fill="#fbbf24" font-size="9">reward ${escHtml(fmtISK(projectPrice))}</text>`;
    }

    const yTicks = [lo, (lo + hi) / 2, hi].map(v => {
      const yy = yAt(v);
      return `
        <line x1="${padL}" y1="${yy.toFixed(1)}" x2="${(padL + plotW).toFixed(1)}" y2="${yy.toFixed(1)}"
              stroke="#1a1e2e" stroke-width="1"/>
        <text x="${padL - 6}" y="${(yy + 3).toFixed(1)}" text-anchor="end"
              fill="#475569" font-size="9">${escHtml(fmtISK(v))}</text>`;
    }).join('');

    const xIdx = [0, Math.floor((n - 1) / 2), n - 1];
    const xTicks = xIdx.map(i => {
      const d = new Date(history[i].date);
      const lbl = `${d.getMonth() + 1}/${d.getDate()}`;
      const anchor = i === 0 ? 'start' : i === n - 1 ? 'end' : 'middle';
      return `<text x="${xAt(i).toFixed(1)}" y="${H - 8}" text-anchor="${anchor}"
                   fill="#475569" font-size="9">${lbl}</text>`;
    }).join('');

    const summary = `
      <div style="display:flex;gap:1rem;font-size:0.7rem;color:#64748b;margin-top:0.3rem;flex-wrap:wrap">
        <span>Latest <b style="color:#e2e8f0">${escHtml(fmtISK(history[n - 1].average))}</b></span>
        <span>90d high <b style="color:#e2e8f0">${escHtml(fmtISK(Math.max(...highs)))}</b></span>
        <span>90d low <b style="color:#e2e8f0">${escHtml(fmtISK(Math.min(...lows)))}</b></span>
      </div>`;

    return open + `
      <svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto;display:block">
        ${yTicks}${band}${line}${refLine}${xTicks}
      </svg>${summary}</div>`;
  }

  // Fetch market history for a project's reward item and inject the chart.
  async function loadPriceHistory(project) {
    const slot = document.getElementById('price-history-slot');
    if (!slot) return;
    try {
      const r = await fetch(`/api/market/${project.market.type_id}/history`);
      if (!r.ok) throw new Error('http ' + r.status);
      const data = await r.json();
      slot.innerHTML = renderPriceHistoryChart(data.history || [], project.market.project_price);
    } catch {
      slot.innerHTML = renderPriceHistoryChart([], null);
    }
  }
```

`fmtISK` and `escHtml` are existing helpers.

- [ ] **Step 2: Rewire `openProjectContributors`**

In `static/index.html`, find this block (lines 1988–1989):

```javascript
      const data = await r.json();
      setModalBody(renderContributors(project, data.contributors || []));
```

Replace it with:

```javascript
      const data = await r.json();
      const hasMarket = !!(project.market && project.market.type_id);
      const priceSlot = hasMarket
        ? '<div id="price-history-slot"><div style="color:#475569;font-size:0.75rem;text-align:center;padding:0.75rem">Loading price history&hellip;</div></div>'
        : '';
      setModalBody(renderContributors(project, data.contributors || []) + priceSlot);
      if (hasMarket) loadPriceHistory(project);
```

- [ ] **Step 3: Sanity-check the page serves**

Run:
```bash
set -a && source .env && set +a && ~/.local/bin/uvicorn main:app --port 8022 >/tmp/uv4.log 2>&1 &
sleep 4
curl -s -o /dev/null -w "page=%{http_code}\n" http://localhost:8022/
curl -s http://localhost:8022/ | grep -c "renderPriceHistoryChart"
curl -s http://localhost:8022/ | grep -c "price-history-slot"
```
Expected: `page=200`, then `2` (definition + call in `loadPriceHistory`), then `2` (the placeholder div id + `getElementById` lookup).

- [ ] **Step 4: Stop the server**

Run: `pkill -f "uvicorn main:app --port 8022"`

- [ ] **Step 5: Commit**

```bash
git add static/index.html
git commit -m "$(cat <<'EOF'
feat: 90-day Jita price history chart in the project modal

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Out of Scope / Follow-up

- Deploy: the user runs `./deploy.sh` separately.
- Not included: price history for closed projects, per-point chart hover tooltips, a price chart on the project cards, volume visualisation, configurable ranges.

## Self-Review

- **Spec coverage:** contributor pie + legend → Task 3; "Others" grouping at >10, edge cases → Task 3 (`renderContributorPie`); `get_market_history` → Task 1; `/api/market/{type_id}/history` route + 6 h cache + session protection → Task 2; 90-day line chart with high-low band + reward reference line + axis ticks + summary labels → Task 4 (`renderPriceHistoryChart`); shown only when `project.market.type_id` exists → Task 4 Step 2 (`hasMarket`); deferred fetch + best-effort error note → Task 4 (`loadPriceHistory`); backend tests → Tasks 1 & 2. All spec sections mapped.
- **Placeholder scan:** no TBD/TODO; every code step is complete.
- **Type consistency:** `get_market_history` (Task 1) is imported and called by `_cached_market_history` (Task 2); the route returns `{type_id, history}` which Task 4's `loadPriceHistory` reads as `data.history`; `renderContributorPie(sorted, total)` (Task 3) is called as `renderContributorPie(sorted, totalContributed)` from `renderContributors`; `renderPriceHistoryChart(history, projectPrice)` (Task 4) is called with `data.history` and `project.market.project_price`; the `price-history-slot` id is written in Task 4 Step 2 and read in `loadPriceHistory` (Task 4 Step 1).
