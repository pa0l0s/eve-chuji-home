# Member Page Character Card + PVP Income Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move Last Login, Total Logins, and Wallet Balance into the Character card; add a new PVP Income (30d) metric computed from the wallet journal; rename the Financials & Skills card to just Skills.

**Architecture:** New `get_character_wallet_journal` (paginated) in `esi.py`. A pure `_sum_pvp_income` filter in `main.py` keeps the PVP-income rule isolated and unit-testable. A 5-minute in-memory per-character cache wraps the journal fetch so the Member page is fast on reload. `/api/member` adds the value as `pvp_income`. Frontend reshapes two cards: Character grows a 2×2 metrics grid; Financials & Skills loses its wallet/login fields and renames to Skills.

**Tech Stack:** FastAPI + httpx (Python), plain HTML/CSS/JS in `static/index.html`, `pytest` + `respx`.

---

## Preconditions

- Branch: work on `master`; commit there.
- Working tree starts clean.
- SSO scope `esi-wallet.read_character_wallet.v1` is already granted (`auth.py:24`). No env / scope change needed.
- Spec: `docs/superpowers/specs/2026-05-28-member-page-character-card-pvp-income-design.md`.

## File Structure

- **Modify** `esi.py` — add `get_character_wallet_journal(character_id, access_token) -> list[dict]` next to `get_wallet`.
- **Modify** `main.py` — add `_sum_pvp_income(journal)`, `_pvp_income_cache` + `_cached_pvp_income(character_id, access_token)`, and wire `pvp_income` into the `/api/member` response.
- **Modify** `static/index.html` — extend `renderCharacterCard` with a 2×2 metrics grid; rename `renderFinancialsCard` → `renderSkillsCard` (drop wallet/login fields, keep SP metrics); update `renderMemberSkeleton` and `renderMemberProfile` call-sites.
- **Create** `tests/test_member_pvp_income.py` — ESI pagination, sum-filter pure-function, `/api/member` integration, and cache-hit tests.

---

## Task 1: ESI `get_character_wallet_journal`

**Files:**
- Modify: `esi.py` (new function near `get_wallet` at line ~83)
- Test: `tests/test_member_pvp_income.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_member_pvp_income.py` with exactly this content:

```python
import pytest
import respx
from httpx import Response

from esi import get_character_wallet_journal


CID = 2122515161
JOURNAL_URL = f"https://esi.evetech.net/latest/characters/{CID}/wallet/journal/"


@respx.mock
async def test_journal_single_page():
    respx.get(JOURNAL_URL).mock(return_value=Response(
        200,
        json=[{"id": 1, "amount": 100, "ref_type": "x", "description": ""}],
        headers={"X-Pages": "1"},
    ))
    entries = await get_character_wallet_journal(CID, "tok")
    assert entries == [{"id": 1, "amount": 100, "ref_type": "x", "description": ""}]


@respx.mock
async def test_journal_walks_multiple_pages():
    route = respx.get(JOURNAL_URL).mock(side_effect=[
        Response(200, json=[{"id": 1}, {"id": 2}], headers={"X-Pages": "2"}),
        Response(200, json=[{"id": 3}],            headers={"X-Pages": "2"}),
    ])
    entries = await get_character_wallet_journal(CID, "tok")
    assert [e["id"] for e in entries] == [1, 2, 3]
    assert route.call_count == 2


@respx.mock
async def test_journal_stops_on_empty_page():
    # Defensive: ESI sometimes omits/zeros X-Pages; an empty list ends the walk.
    respx.get(JOURNAL_URL).mock(return_value=Response(200, json=[]))
    entries = await get_character_wallet_journal(CID, "tok")
    assert entries == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_member_pvp_income.py -v`
Expected: FAIL — `ImportError: cannot import name 'get_character_wallet_journal' from 'esi'`.

- [ ] **Step 3: Add `get_character_wallet_journal` to `esi.py`**

In `esi.py`, find `get_wallet` (near line 83). Immediately **after** that function (before `get_skills` near line 93), insert:

```python


async def get_character_wallet_journal(character_id: int, access_token: str) -> list[dict]:
    """All wallet-journal entries (ESI keeps ~30 days). Walks pages until empty."""
    result: list[dict] = []
    page = 1
    async with httpx.AsyncClient() as client:
        while True:
            r = await client.get(
                f"{ESI_BASE}/characters/{character_id}/wallet/journal/",
                headers={"Authorization": f"Bearer {access_token}"},
                params={"page": page},
            )
            r.raise_for_status()
            entries = r.json() or []
            if not entries:
                break
            result.extend(entries)
            total_pages = int(r.headers.get("X-Pages", "1"))
            if page >= total_pages:
                break
            page += 1
    return result
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_member_pvp_income.py -v`
Expected: PASS — 3 passed.

- [ ] **Step 5: Commit**

```bash
git add esi.py tests/test_member_pvp_income.py
git commit -m "$(cat <<'EOF'
feat: add get_character_wallet_journal ESI helper

Paginated walker; stops on empty page or when X-Pages is reached.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: `_sum_pvp_income` filter + `_cached_pvp_income` cache

**Files:**
- Modify: `main.py` (add helpers near the existing `_contributors_cache` block at line ~628)
- Test: `tests/test_member_pvp_income.py` (append)

- [ ] **Step 1: Write the failing tests**

Append exactly this to `tests/test_member_pvp_income.py`:

```python
import time

import main as main_module
from main import _sum_pvp_income, _cached_pvp_income


# Sample journal entries shared across tests below.
SAMPLE_JOURNAL = [
    {  # match: ref_type AND description substring
        "id": 1, "amount": 371_000_000,
        "ref_type": "corporation_account_withdrawal",
        "description": "Cerberus Agnon transferred cash from corp account to player",
    },
    {  # second match
        "id": 2, "amount": 50_000_000,
        "ref_type": "corporation_account_withdrawal",
        "description": "Cerberus Agnon transferred cash again",
    },
    {  # same ref_type, different name -> not a match
        "id": 3, "amount": 99_999,
        "ref_type": "corporation_account_withdrawal",
        "description": "Someone Else transferred cash",
    },
    {  # mentions Cerberus Agnon but wrong ref_type -> not a match
        "id": 4, "amount": 1,
        "ref_type": "player_donation",
        "description": "Gift from Cerberus Agnon",
    },
    {  # null description -> must not crash
        "id": 5, "amount": 7,
        "ref_type": "corporation_account_withdrawal",
        "description": None,
    },
]


def test_sum_pvp_income_includes_only_matching_entries():
    assert _sum_pvp_income(SAMPLE_JOURNAL) == 371_000_000 + 50_000_000


def test_sum_pvp_income_empty():
    assert _sum_pvp_income([]) == 0


def test_sum_pvp_income_none_description_safe():
    entry_with_none = [{
        "id": 1, "amount": 5,
        "ref_type": "corporation_account_withdrawal",
        "description": None,
    }]
    assert _sum_pvp_income(entry_with_none) == 0


@pytest.fixture(autouse=True)
def _reset_pvp_cache():
    main_module._pvp_income_cache.clear()
    yield
    main_module._pvp_income_cache.clear()


@respx.mock
async def test_cached_pvp_income_caches_within_ttl():
    route = respx.get(JOURNAL_URL).mock(return_value=Response(
        200, json=SAMPLE_JOURNAL, headers={"X-Pages": "1"},
    ))
    first  = await _cached_pvp_income(CID, "tok")
    second = await _cached_pvp_income(CID, "tok")
    assert first == 371_000_000 + 50_000_000
    assert second == first
    assert route.call_count == 1  # cache hit on second call


@respx.mock
async def test_cached_pvp_income_refetches_after_ttl():
    route = respx.get(JOURNAL_URL).mock(return_value=Response(
        200, json=SAMPLE_JOURNAL, headers={"X-Pages": "1"},
    ))
    # Prime the cache then expire the entry by rewinding cached_at.
    await _cached_pvp_income(CID, "tok")
    cached_at, amount = main_module._pvp_income_cache[CID]
    main_module._pvp_income_cache[CID] = (cached_at - 301, amount)
    again = await _cached_pvp_income(CID, "tok")
    assert again == amount
    assert route.call_count == 2
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_member_pvp_income.py -v`
Expected: the 5 new tests FAIL — `ImportError: cannot import name '_sum_pvp_income' from 'main'`.

- [ ] **Step 3: Add `_sum_pvp_income` and the cache helpers to `main.py`**

In `main.py`, find the existing `_contributors_cache` block (near line 628). Immediately **after** that block (after the existing `_cached_project_contributors` function, before the next top-level definition), insert:

```python


_pvp_income_cache: dict[int, tuple[float, float]] = {}  # cid -> (cached_at, amount)
_PVP_INCOME_TTL = 300.0


def _sum_pvp_income(journal: list[dict]) -> float:
    return sum(
        e.get("amount", 0) for e in journal
        if e.get("ref_type") == "corporation_account_withdrawal"
        and "Cerberus Agnon" in (e.get("description") or "")
    )


async def _cached_pvp_income(character_id: int, access_token: str) -> float:
    now = time.time()
    hit = _pvp_income_cache.get(character_id)
    if hit and (now - hit[0]) < _PVP_INCOME_TTL:
        return hit[1]
    journal = await get_character_wallet_journal(character_id, access_token)
    amount = _sum_pvp_income(journal)
    _pvp_income_cache[character_id] = (now, amount)
    return amount
```

- [ ] **Step 4: Import the new ESI helper in `main.py`**

In `main.py`, find the line that imports `get_wallet` from `esi` (near line 20). It currently reads:

```python
    get_valid_token, get_character, get_wallet, get_skills,
```

Change it to:

```python
    get_valid_token, get_character, get_wallet, get_character_wallet_journal, get_skills,
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_member_pvp_income.py -v`
Expected: PASS — 8 passed total (3 from Task 1 + 5 new).

- [ ] **Step 6: Commit**

```bash
git add main.py tests/test_member_pvp_income.py
git commit -m "$(cat <<'EOF'
feat: add _sum_pvp_income filter + 5-min cached _cached_pvp_income

Filters wallet journal for corporation_account_withdrawal entries whose
description mentions "Cerberus Agnon" (the corp's PVP-payout signature),
sums their amount. Per-character in-memory cache with 300s TTL.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: `/api/member` integration

**Files:**
- Modify: `main.py` (the `asyncio.gather` block at lines 1207–1218 and the response dict at lines 1289–1299)
- Test: `tests/test_member_pvp_income.py` (append)

- [ ] **Step 1: Write the failing tests**

Append exactly this to `tests/test_member_pvp_income.py`:

```python
from db import upsert_token
from auth import make_session_cookie


async def _user_cookie(character_id: int = CID) -> dict:
    await upsert_token(
        character_id=character_id, character_name="Pilot",
        access_token="acc", refresh_token="ref",
        expires_at=time.time() + 1200, corporation_id=98340844,
    )
    return {"session": make_session_cookie(character_id)}


# Catch-all stubs for every endpoint /api/member fans out to, so the test
# isolates the pvp_income path even if other ESI calls happen. Each `respx`
# route returns the smallest plausible payload so _safe() does NOT mark
# the section as errored.
def _stub_all_other_member_endpoints():
    base = "https://esi.evetech.net/latest/characters"
    cb = f"{base}/{CID}"
    respx.get(f"{cb}/").mock(return_value=Response(200, json={
        "name": "Pilot", "corporation_id": 98340844, "security_status": 0.5,
    }))
    respx.get(f"{cb}/wallet/").mock(return_value=Response(200, json=0.0))
    respx.get(f"{cb}/skills/").mock(return_value=Response(200, json={
        "skills": [], "total_sp": 0, "unallocated_sp": 0,
    }))
    respx.get(f"{cb}/location/").mock(return_value=Response(200, json={}))
    respx.get(f"{cb}/online/").mock(return_value=Response(200, json={
        "online": False, "last_login": "2026-05-28T10:00:00Z",
        "last_logout": None, "logins": 42,
    }))
    respx.get(f"{cb}/ship/").mock(return_value=Response(200, json={}))
    respx.get(f"{cb}/attributes/").mock(return_value=Response(200, json={}))
    respx.get(f"{cb}/skillqueue/").mock(return_value=Response(200, json=[]))


@respx.mock
async def test_member_response_includes_pvp_income(client):
    _stub_all_other_member_endpoints()
    respx.get(JOURNAL_URL).mock(return_value=Response(
        200, json=SAMPLE_JOURNAL, headers={"X-Pages": "1"},
    ))
    cookies = await _user_cookie()
    r = await client.get("/api/member", cookies=cookies)
    assert r.status_code == 200
    body = r.json()
    assert body["pvp_income"]["data"] == 371_000_000 + 50_000_000
    assert body["pvp_income"]["error"] is None


@respx.mock
async def test_member_pvp_income_uses_cache_across_requests(client):
    _stub_all_other_member_endpoints()
    journal_route = respx.get(JOURNAL_URL).mock(return_value=Response(
        200, json=SAMPLE_JOURNAL, headers={"X-Pages": "1"},
    ))
    cookies = await _user_cookie()
    await client.get("/api/member", cookies=cookies)
    await client.get("/api/member", cookies=cookies)
    assert journal_route.call_count == 1  # second hit served from cache
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_member_pvp_income.py -v`
Expected: the 2 new tests FAIL — `KeyError: 'pvp_income'` (the response doesn't include the field yet).

- [ ] **Step 3: Extend the `asyncio.gather` and the response dict in `/api/member`**

In `main.py`, find the gather block at lines 1207–1218. It currently reads:

```python
    (char_data, char_err), (wallet, wallet_err), (skills, skills_err), \
    (location, loc_err),   (online, online_err),  (ship, ship_err), \
    (attributes, attr_err), (skillqueue, sq_err) = await asyncio.gather(
        _safe(get_character(character_id, access_token)),
        _safe(get_wallet(character_id, access_token)),
        _safe(get_skills(character_id, access_token)),
        _safe(get_character_location(character_id, access_token)),
        _safe(get_character_online(character_id, access_token)),
        _safe(get_character_ship(character_id, access_token)),
        _safe(get_character_attributes(character_id, access_token)),
        _safe(get_character_skillqueue(character_id, access_token)),
    )
```

Change it to:

```python
    (char_data, char_err), (wallet, wallet_err), (skills, skills_err), \
    (location, loc_err),   (online, online_err),  (ship, ship_err), \
    (attributes, attr_err), (skillqueue, sq_err), (pvp_income, pvp_income_err) = await asyncio.gather(
        _safe(get_character(character_id, access_token)),
        _safe(get_wallet(character_id, access_token)),
        _safe(get_skills(character_id, access_token)),
        _safe(get_character_location(character_id, access_token)),
        _safe(get_character_online(character_id, access_token)),
        _safe(get_character_ship(character_id, access_token)),
        _safe(get_character_attributes(character_id, access_token)),
        _safe(get_character_skillqueue(character_id, access_token)),
        _safe(_cached_pvp_income(character_id, access_token)),
    )
```

In the same function, find the response dict near line 1289–1299. It currently reads:

```python
    return {
        "character_id": character_id,
        "profile":    _wrap(profile,         char_err),
        "wallet":     _wrap(wallet,          wallet_err),
        "skills":     _wrap(skills_summary,  skills_err),
        "location":   _wrap(location_block,  loc_err),
        "online":     _wrap(online_block,    online_err),
        "ship":       _wrap(ship_block,      ship_err),
        "attributes": _wrap(attributes,      attr_err),
        "skillqueue": _wrap(skillqueue_block, sq_err),
    }
```

Change it to:

```python
    return {
        "character_id": character_id,
        "profile":    _wrap(profile,         char_err),
        "wallet":     _wrap(wallet,          wallet_err),
        "skills":     _wrap(skills_summary,  skills_err),
        "location":   _wrap(location_block,  loc_err),
        "online":     _wrap(online_block,    online_err),
        "ship":       _wrap(ship_block,      ship_err),
        "attributes": _wrap(attributes,      attr_err),
        "skillqueue": _wrap(skillqueue_block, sq_err),
        "pvp_income": _wrap(pvp_income,      pvp_income_err),
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_member_pvp_income.py -v`
Expected: PASS — 10 passed total (3 from Task 1 + 5 from Task 2 + 2 new).

Run: `pytest -q`
Expected: PASS — full suite green.

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_member_pvp_income.py
git commit -m "$(cat <<'EOF'
feat: expose pvp_income on /api/member

Folds _cached_pvp_income into the asyncio.gather alongside the other
character ESI fetches; the result is wrapped via _wrap and surfaced
as the new pvp_income section on the response.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Frontend — Character card 2×2 metrics + Skills card

**Files:**
- Modify: `static/index.html` (skeleton at line ~3024, profile dispatch at line ~3053, `renderCharacterCard` at lines 3089–3124, `renderFinancialsCard` at lines 3182–3240)

This task has no automated frontend tests — verify via static checks.

- [ ] **Step 1: Update the skeleton title**

In `static/index.html`, find `renderMemberSkeleton` (around line 3018). Change the line that currently reads:

```html
      <div class="card"><div class="card-title">Financials &amp; Skills</div>
```

to:

```html
      <div class="card"><div class="card-title">Skills</div>
```

- [ ] **Step 2: Update the profile dispatch call-site**

In `static/index.html`, find `renderMemberProfile` (around line 3043). It calls the three card renderers in order. Change the line that currently reads:

```javascript
      ${renderFinancialsCard(m)}
```

to:

```javascript
      ${renderSkillsCard(m)}
```

- [ ] **Step 3: Extend `renderCharacterCard` with a 2×2 metrics grid**

In `static/index.html`, replace the entire `renderCharacterCard` function (lines 3089–3124) with:

```javascript
  function renderCharacterCard(m) {
    const charErr = sectionError(m.profile);
    const onlineErr = sectionError(m.online);
    const walletErr = sectionError(m.wallet);
    const pvpErr = sectionError(m.pvp_income);
    const p = sectionData(m.profile);
    const o = sectionData(m.online);
    const wallet = sectionData(m.wallet);
    const pvp = sectionData(m.pvp_income);
    if (charErr && !p) {
      return `<div class="card"><div class="card-title">Character</div>${cardErrorBlock(charErr)}</div>`;
    }
    const secStatColor = p && p.security_status >= 0 ? '#4ade80' : '#f87171';
    const secSign      = p && p.security_status >= 0 ? '+' : '';
    const onlineDot = o?.is_online
      ? '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#4ade80;box-shadow:0 0 6px #4ade80;margin-right:0.4rem"></span>'
      : '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#475569;margin-right:0.4rem"></span>';
    const onlineLabel = onlineErr ? 'Online status unavailable' : (o?.is_online ? 'Online' : 'Offline');

    const w = walletDisplay(wallet);
    const walletCell = walletErr
      ? `<div class="metric"><div class="label">Wallet Balance</div><div class="val">–</div></div>`
      : `<div class="metric">
           <div class="label">Wallet Balance</div>
           <div class="val ${w.sizeCls}" title="${w.tooltip}">
             ${w.value}<span class="unit">ISK</span>
           </div>
         </div>`;
    const pvpView = walletDisplay(pvp);
    const pvpCell = pvpErr
      ? `<div class="metric"><div class="label">PVP Income (30d)</div><div class="val">–</div></div>`
      : `<div class="metric">
           <div class="label">PVP Income (30d)</div>
           <div class="val ${pvpView.sizeCls}" title="Corp Account Withdrawal from Cerberus Agnon, last 30 days">
             ${pvpView.value}<span class="unit">ISK</span>
           </div>
         </div>`;
    const lastLoginCell = `
      <div class="metric">
        <div class="label">Last Login</div>
        <div style="color:#94a3b8;font-size:0.8rem;margin-top:0.15rem">
          ${o?.last_login ? new Date(o.last_login).toLocaleString() : '–'}
        </div>
      </div>`;
    const totalLoginsCell = `
      <div class="metric">
        <div class="label">Total Logins</div>
        <div style="color:#94a3b8;font-size:0.8rem;margin-top:0.15rem">
          ${o?.logins != null ? fmtNum(o.logins) : '–'}
        </div>
      </div>`;

    return `
      <div class="card">
        <div class="card-title">Character</div>
        <div class="char-card">
          <img class="char-portrait"
               src="https://images.evetech.net/characters/${m.character_id}/portrait?size=128" alt="">
          <div class="char-info">
            <div class="name" style="display:flex;align-items:center;gap:0.4rem">
              ${escHtml(p?.character_name || 'Unknown')}
              ${characterZkillLink(m.character_id)}
            </div>
            <div class="corp" id="corp-name-display">Corp ID: ${p?.corporation_id ?? '–'}</div>
            <div class="sec-status" style="color:${secStatColor}">
              Security Status: ${p ? secSign + p.security_status.toFixed(1) : '–'}
            </div>
            <div style="font-size:0.8rem;color:#94a3b8;margin-top:0.4rem">
              ${onlineDot}${onlineLabel}
            </div>
          </div>
        </div>
        <div class="metrics" style="margin-top:0.75rem">
          ${walletCell}
          ${pvpCell}
          ${lastLoginCell}
          ${totalLoginsCell}
        </div>
      </div>`;
  }
```

- [ ] **Step 4: Rename `renderFinancialsCard` → `renderSkillsCard` and drop wallet/login fields**

In `static/index.html`, replace the entire `renderFinancialsCard` function (lines 3182–3240) with:

```javascript
  function renderSkillsCard(m) {
    const skillsErr = sectionError(m.skills);
    const skills = sectionData(m.skills);
    const usedSp = skills?.total_sp || 0;
    const unusedSp = skills?.unallocated_sp || 0;
    const skillsCell = skillsErr
      ? `<div class="metric">${cardErrorBlock(skillsErr)}</div>`
      : `<div class="metric">
           <div class="label">Used Skillpoints</div>
           <div class="val">${fmtSP(usedSp)}<span class="unit">SP</span></div>
         </div>`;
    const unusedSpCell = skillsErr ? '' : `<div class="metric">
           <div class="label">Unused Skillpoints</div>
           <div class="val">${fmtSP(unusedSp)}<span class="unit">SP</span></div>
         </div>`;
    const totalSpCell = skillsErr ? '' : `<div class="metric">
           <div class="label">Total Skillpoints</div>
           <div class="val">${fmtSP(usedSp + unusedSp)}<span class="unit">SP</span></div>
         </div>`;

    return `
      <div class="card">
        <div class="card-title">Skills</div>
        <div class="metrics">
          ${skillsCell}
          ${unusedSpCell}
          ${totalSpCell}
        </div>
      </div>`;
  }
```

- [ ] **Step 5: Static verification**

Confirm via grep:
- `grep -n "renderSkillsCard\b" static/index.html` returns at least the definition and the call-site (≥2 hits).
- `grep -n "renderFinancialsCard\b" static/index.html` returns 0 hits.
- `grep -n "Financials &amp; Skills" static/index.html` returns 0 hits.
- `grep -n "PVP Income (30d)" static/index.html` returns the new label.
- `grep -nc "Character card" static/index.html` — informational.

- [ ] **Step 6: Run the test suite**

Run: `pytest -q`
Expected: PASS — no Python changes, but ensures the suite is still green.

- [ ] **Step 7: Manual UI verification (static checks only — no browser)**

You cannot start a browser. Verify the resulting JavaScript by re-reading the two functions you wrote and confirming:
- `renderCharacterCard` reads four `m.<section>` values (`profile`, `online`, `wallet`, `pvp_income`) and renders 4 metric cells beneath the existing horizontal char-info block.
- `renderSkillsCard` reads only `m.skills`, renders 3 SP metric cells, titled "Skills".
- No reference remains to wallet / online inside `renderSkillsCard`.
- `renderMemberProfile` calls `renderSkillsCard(m)` (Step 2 update).
- `renderMemberSkeleton` says "Skills" (Step 1 update).

- [ ] **Step 8: Commit**

```bash
git add static/index.html
git commit -m "$(cat <<'EOF'
feat: move wallet/logins into Character card; rename Financials & Skills -> Skills

Character card grows a 2x2 metrics grid: Wallet Balance, PVP Income (30d),
Last Login, Total Logins. The former Financials & Skills card collapses
to just three skill-point metrics under the title "Skills".

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Done

Four independent commits. After Task 4:

- `git log --oneline -5` shows the four feature commits plus the earlier spec commit.
- `pytest -q` passes.
- Member page Character card carries identity + wallet + PVP income + login stats; Skills card carries only SP metrics, titled "Skills".
- PVP-income computation is per-character cached for 5 minutes to keep Member-page reloads fast.
