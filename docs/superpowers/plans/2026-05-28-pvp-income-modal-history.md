# PVP Income Modal + All-Time History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Character card's PVP Income (30d) metric clickable; clicking opens a modal showing the all-time list of matching wallet-journal entries with pagination and a totals row.

**Architecture:** New `pvp_income_entries` SQLite table keyed by `(character_id, journal_id)`. The existing `_cached_pvp_income` cache-miss path persists matching entries via `ON CONFLICT DO NOTHING` upserts (idempotent, never overwrites). A new `GET /api/me/pvp-income?page=N` endpoint serves paginated entries plus `total_count` and `total_amount` (across all entries, independent of pagination). Frontend reuses the existing `showModal` / `setModalBody` modal infrastructure already used by the project-contributors popup.

**Tech Stack:** FastAPI + aiosqlite (Python), plain HTML/CSS/JS in `static/index.html`, `pytest` + `respx`.

---

## Preconditions

- Branch: work on `master`; commit there.
- Working tree starts clean.
- Spec: `docs/superpowers/specs/2026-05-28-pvp-income-modal-history-design.md`.
- SSO scope `esi-wallet.read_character_wallet.v1` already granted.
- The `_cached_pvp_income`, `_sum_pvp_income`, and `_pvp_income_cache` machinery exists in `main.py` from the previous PVP-Income feature.
- The modal infrastructure (`showModal(title, body)`, `setModalBody(html)`, `closeModal()`) exists in `static/index.html` around line 1787.

## File Structure

- **Modify** `db.py` — new `project_created_cache`-style CREATE TABLE + INDEX block for `pvp_income_entries`, plus three accessors (`upsert_pvp_entries_batch`, `list_pvp_entries_page`, `sum_pvp_entries`).
- **Modify** `main.py` — extract `_is_pvp_entry` predicate from the existing `_sum_pvp_income`; extend `_cached_pvp_income` to upsert matching entries on cache miss; add `GET /api/me/pvp-income` endpoint.
- **Modify** `static/index.html` — `renderCharacterCard`'s PVP cell becomes clickable; add `openPVPIncomeModal(page)` JS function that fetches the endpoint and renders table + totals + pager into the existing modal.
- **Create** `tests/test_pvp_income_history.py` — DB accessor tests, persistence-on-cache-miss tests, endpoint tests, auth test.

---

## Task 1: `pvp_income_entries` table + DB accessors

**Files:**
- Modify: `db.py` (new CREATE TABLE + INDEX block near line 89, three new accessors appended at end of file)
- Test: `tests/test_pvp_income_history.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_pvp_income_history.py` with exactly this content:

```python
import pytest

from db import (
    init_db,
    upsert_pvp_entries_batch,
    list_pvp_entries_page,
    sum_pvp_entries,
)


CID = 2122515161


@pytest.fixture(autouse=True)
async def _db():
    await init_db()


async def test_init_db_idempotent_for_pvp_entries():
    await init_db()  # second run must not raise


async def test_empty_listing():
    rows, total = await list_pvp_entries_page(CID, limit=10, offset=0)
    assert rows == []
    assert total == 0
    assert await sum_pvp_entries(CID) == 0


async def test_upsert_batch_empty_is_noop():
    await upsert_pvp_entries_batch(CID, [])
    rows, total = await list_pvp_entries_page(CID, limit=10, offset=0)
    assert rows == [] and total == 0


async def test_upsert_round_trip_newest_first():
    await upsert_pvp_entries_batch(CID, [
        {"id": 1, "date": "2026-01-01T00:00:00Z", "amount": 100.0,
         "reason": "Astrahus", "description": "Cerberus Agnon ..."},
        {"id": 2, "date": "2026-03-01T00:00:00Z", "amount": 200.0,
         "reason": "PVP1", "description": "Cerberus Agnon ..."},
        {"id": 3, "date": "2026-02-01T00:00:00Z", "amount": 50.0,
         "reason": "PVP2", "description": "Cerberus Agnon ..."},
    ])
    rows, total = await list_pvp_entries_page(CID, limit=10, offset=0)
    assert total == 3
    assert [r["amount"] for r in rows] == [200.0, 50.0, 100.0]  # by date desc
    # Each row exposes only the columns the modal needs.
    assert set(rows[0].keys()) == {"date", "amount", "reason"}


async def test_upsert_conflict_does_not_overwrite():
    await upsert_pvp_entries_batch(CID, [
        {"id": 1, "date": "2026-01-01T00:00:00Z", "amount": 100.0,
         "reason": "Astrahus", "description": "first version"},
    ])
    # Re-upsert with different reason/description — must NOT clobber the first row.
    await upsert_pvp_entries_batch(CID, [
        {"id": 1, "date": "2026-01-01T00:00:00Z", "amount": 999.0,
         "reason": "DIFFERENT", "description": "second version"},
    ])
    rows, total = await list_pvp_entries_page(CID, limit=10, offset=0)
    assert total == 1
    assert rows[0]["amount"] == 100.0
    assert rows[0]["reason"] == "Astrahus"


async def test_pagination_limit_offset():
    await upsert_pvp_entries_batch(CID, [
        {"id": i, "date": f"2026-01-{i:02d}T00:00:00Z", "amount": float(i),
         "reason": f"r{i}", "description": ""}
        for i in range(1, 6)  # 5 rows
    ])
    page1, total = await list_pvp_entries_page(CID, limit=2, offset=0)
    page2, _    = await list_pvp_entries_page(CID, limit=2, offset=2)
    page3, _    = await list_pvp_entries_page(CID, limit=2, offset=4)
    assert total == 5
    assert [r["amount"] for r in page1] == [5.0, 4.0]
    assert [r["amount"] for r in page2] == [3.0, 2.0]
    assert [r["amount"] for r in page3] == [1.0]


async def test_sum_pvp_entries():
    await upsert_pvp_entries_batch(CID, [
        {"id": 1, "date": "2026-01-01T00:00:00Z", "amount": 100.0,
         "reason": "", "description": ""},
        {"id": 2, "date": "2026-02-01T00:00:00Z", "amount": 250.5,
         "reason": "", "description": ""},
    ])
    assert await sum_pvp_entries(CID) == 350.5


async def test_per_character_isolation():
    await upsert_pvp_entries_batch(CID, [
        {"id": 1, "date": "2026-01-01T00:00:00Z", "amount": 100.0,
         "reason": "", "description": ""},
    ])
    await upsert_pvp_entries_batch(999, [
        {"id": 1, "date": "2026-01-01T00:00:00Z", "amount": 5.0,
         "reason": "", "description": ""},
    ])
    rows_a, total_a = await list_pvp_entries_page(CID, limit=10, offset=0)
    rows_b, total_b = await list_pvp_entries_page(999, limit=10, offset=0)
    assert total_a == 1 and rows_a[0]["amount"] == 100.0
    assert total_b == 1 and rows_b[0]["amount"] == 5.0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_pvp_income_history.py -v`
Expected: FAIL — `ImportError: cannot import name 'upsert_pvp_entries_batch' from 'db'`.

- [ ] **Step 3: Add the CREATE TABLE + INDEX block in `init_db`**

In `db.py`, find the `ship_types_cache` CREATE TABLE (near line 89). Immediately **after** that block (i.e. after the `project_created_cache` CREATE TABLE that the prior plan added; if you can't find that block, look just before the `# Migrations:` comment near line 96), insert:

```python
        await db.execute("""
            CREATE TABLE IF NOT EXISTS pvp_income_entries (
                character_id INTEGER NOT NULL,
                journal_id   INTEGER NOT NULL,
                date         TEXT    NOT NULL,
                amount       REAL    NOT NULL,
                reason       TEXT,
                description  TEXT,
                PRIMARY KEY (character_id, journal_id)
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_pvp_income_char_date
            ON pvp_income_entries (character_id, date DESC)
        """)
```

- [ ] **Step 4: Add the accessors**

In `db.py`, at the end of the file, append:

```python


async def upsert_pvp_entries_batch(character_id: int, entries: list[dict]) -> None:
    if not entries:
        return
    rows = [
        (character_id, e["id"], e["date"], e["amount"],
         e.get("reason"), e.get("description"))
        for e in entries
    ]
    async with aiosqlite.connect(_db_path()) as db:
        await db.executemany(
            """INSERT INTO pvp_income_entries
                 (character_id, journal_id, date, amount, reason, description)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(character_id, journal_id) DO NOTHING""",
            rows,
        )
        await db.commit()


async def list_pvp_entries_page(
    character_id: int, limit: int, offset: int
) -> tuple[list[dict], int]:
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT date, amount, reason
                 FROM pvp_income_entries
                WHERE character_id = ?
                ORDER BY date DESC
                LIMIT ? OFFSET ?""",
            (character_id, limit, offset),
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
        async with db.execute(
            "SELECT COUNT(*) FROM pvp_income_entries WHERE character_id = ?",
            (character_id,),
        ) as cur:
            (total,) = await cur.fetchone()
    return rows, total


async def sum_pvp_entries(character_id: int) -> float:
    async with aiosqlite.connect(_db_path()) as db:
        async with db.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM pvp_income_entries "
            "WHERE character_id = ?",
            (character_id,),
        ) as cur:
            (total,) = await cur.fetchone()
    return float(total)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_pvp_income_history.py -v`
Expected: PASS — 8 passed.

- [ ] **Step 6: Commit**

```bash
git add db.py tests/test_pvp_income_history.py
git commit -m "$(cat <<'EOF'
feat: add pvp_income_entries table + accessors

(character_id, journal_id) PK, idx on (character_id, date DESC).
upsert_pvp_entries_batch is ON CONFLICT DO NOTHING (first-write wins).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: `_is_pvp_entry` refactor + persistence in `_cached_pvp_income`

**Files:**
- Modify: `main.py` (refactor `_sum_pvp_income` near line ~640; extend `_cached_pvp_income` to upsert matches)
- Test: `tests/test_pvp_income_history.py` (append)

- [ ] **Step 1: Write the failing tests**

Append exactly this to `tests/test_pvp_income_history.py`:

```python
import respx
from httpx import Response


JOURNAL_URL = f"https://esi.evetech.net/latest/characters/{CID}/wallet/journal/"


SAMPLE_JOURNAL = [
    {  # match
        "id": 11, "amount": 100.0,
        "date": "2026-05-01T00:00:00Z",
        "ref_type": "corporation_account_withdrawal",
        "description": "Cerberus Agnon transferred cash to player",
        "reason": "Astrahus",
    },
    {  # match
        "id": 12, "amount": 200.0,
        "date": "2026-05-02T00:00:00Z",
        "ref_type": "corporation_account_withdrawal",
        "description": "Cerberus Agnon transferred cash again",
        "reason": "PVP",
    },
    {  # non-match: ref_type
        "id": 13, "amount": 999.0,
        "date": "2026-05-03T00:00:00Z",
        "ref_type": "player_donation",
        "description": "From Cerberus Agnon",
        "reason": "Gift",
    },
    {  # non-match: description
        "id": 14, "amount": 999.0,
        "date": "2026-05-04T00:00:00Z",
        "ref_type": "corporation_account_withdrawal",
        "description": "Someone Else transferred cash",
        "reason": "x",
    },
]


@pytest.fixture(autouse=True)
def _main_module(set_env):
    import main
    main._pvp_income_cache.clear()
    yield main
    main._pvp_income_cache.clear()


@respx.mock
async def test_cached_pvp_income_persists_only_matching_entries(_main_module):
    respx.get(JOURNAL_URL).mock(return_value=Response(
        200, json=SAMPLE_JOURNAL, headers={"X-Pages": "1"},
    ))
    sum_returned = await _main_module._cached_pvp_income(CID, "tok")
    assert sum_returned == 300.0  # only the two matching entries
    rows, total = await list_pvp_entries_page(CID, limit=10, offset=0)
    assert total == 2
    assert {int(r["amount"]) for r in rows} == {100, 200}


@respx.mock
async def test_cached_pvp_income_cache_hit_skips_db_write(_main_module):
    # Pre-populate the per-character cache so the function returns without
    # touching ESI or the DB.
    import time
    _main_module._pvp_income_cache[CID] = (time.time(), 42.0)
    # Deliberately register NO routes; respx would raise if a request fired.
    result = await _main_module._cached_pvp_income(CID, "tok")
    assert result == 42.0
    rows, total = await list_pvp_entries_page(CID, limit=10, offset=0)
    assert total == 0  # cache hit did not write anything


@respx.mock
async def test_cached_pvp_income_persists_idempotently(_main_module):
    respx.get(JOURNAL_URL).mock(return_value=Response(
        200, json=SAMPLE_JOURNAL, headers={"X-Pages": "1"},
    ))
    await _main_module._cached_pvp_income(CID, "tok")
    # Expire the cache and call again; the second cache miss must not
    # duplicate the rows we already persisted.
    cached_at, amount = _main_module._pvp_income_cache[CID]
    _main_module._pvp_income_cache[CID] = (cached_at - 301, amount)
    await _main_module._cached_pvp_income(CID, "tok")
    _, total = await list_pvp_entries_page(CID, limit=10, offset=0)
    assert total == 2
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `unset CORP_ID && pytest tests/test_pvp_income_history.py -v`
Expected: the 3 new tests FAIL — the DB row count stays at 0 because `_cached_pvp_income` doesn't yet persist anything.

- [ ] **Step 3: Extract `_is_pvp_entry` and update `_sum_pvp_income` in `main.py`**

In `main.py`, find the existing `_sum_pvp_income` function (added by the previous PVP-Income feature; near line 640, right after `_cached_project_contributors`). It currently reads:

```python
def _sum_pvp_income(journal: list[dict]) -> float:
    return sum(
        e.get("amount", 0) for e in journal
        if e.get("ref_type") == "corporation_account_withdrawal"
        and "Cerberus Agnon" in (e.get("description") or "")
    )
```

Replace it with:

```python
def _is_pvp_entry(e: dict) -> bool:
    return (
        e.get("ref_type") == "corporation_account_withdrawal"
        and "Cerberus Agnon" in (e.get("description") or "")
    )


def _sum_pvp_income(journal: list[dict]) -> float:
    return sum(e.get("amount", 0) for e in journal if _is_pvp_entry(e))
```

- [ ] **Step 4: Extend `_cached_pvp_income` with persistence**

In `main.py`, find `_cached_pvp_income` (just after `_sum_pvp_income`). It currently reads:

```python
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

Replace it with:

```python
async def _cached_pvp_income(character_id: int, access_token: str) -> float:
    now = time.time()
    hit = _pvp_income_cache.get(character_id)
    if hit and (now - hit[0]) < _PVP_INCOME_TTL:
        return hit[1]
    journal = await get_character_wallet_journal(character_id, access_token)
    matches = [e for e in journal if _is_pvp_entry(e)]
    if matches:
        await database.upsert_pvp_entries_batch(character_id, matches)
    amount = sum(e.get("amount", 0) for e in matches)
    _pvp_income_cache[character_id] = (now, amount)
    return amount
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `unset CORP_ID && pytest tests/test_pvp_income_history.py -v`
Expected: PASS — 11 passed total (8 from Task 1 + 3 new).

Run: `unset CORP_ID && pytest -q`
Expected: full suite green (the pre-existing PVP-income tests in `tests/test_member_pvp_income.py` still pass — `_sum_pvp_income`'s behaviour is unchanged after the refactor).

- [ ] **Step 6: Commit**

```bash
git add main.py tests/test_pvp_income_history.py
git commit -m "$(cat <<'EOF'
feat: persist matching PVP entries inside _cached_pvp_income

Extracts _is_pvp_entry predicate, upserts matches via ON CONFLICT DO NOTHING
on every cache miss. Cache hits never touch ESI or the DB. 30-day sum
returned by the function is unchanged.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: `GET /api/me/pvp-income` endpoint

**Files:**
- Modify: `main.py` (new route placed near `/api/me/wallet` at line ~1302)
- Test: `tests/test_pvp_income_history.py` (append)

- [ ] **Step 1: Write the failing tests**

Append exactly this to `tests/test_pvp_income_history.py`:

```python
import time

from db import upsert_token
from auth import make_session_cookie


async def _user_cookie(character_id: int = CID) -> dict:
    await upsert_token(
        character_id=character_id, character_name="Pilot",
        access_token="acc", refresh_token="ref",
        expires_at=time.time() + 1200, corporation_id=98340844,
    )
    return {"session": make_session_cookie(character_id)}


async def test_pvp_income_endpoint_requires_auth(client):
    r = await client.get("/api/me/pvp-income")
    assert r.status_code == 401


async def test_pvp_income_endpoint_empty(client):
    cookies = await _user_cookie()
    r = await client.get("/api/me/pvp-income", cookies=cookies)
    assert r.status_code == 200
    body = r.json()
    assert body["entries"] == []
    assert body["total_count"] == 0
    assert body["total_amount"] == 0
    assert body["page"] == 1
    assert body["page_size"] == 20
    assert body["total_pages"] == 1  # at least 1, even when empty


async def test_pvp_income_endpoint_paged(client):
    # 25 rows ⇒ pages 1 (20) and 2 (5).
    await upsert_pvp_entries_batch(CID, [
        {"id": i, "date": f"2026-01-{i:02d}T00:00:00Z", "amount": float(i),
         "reason": f"r{i}", "description": ""}
        for i in range(1, 26)
    ])
    cookies = await _user_cookie()

    r1 = await client.get("/api/me/pvp-income?page=1", cookies=cookies)
    body1 = r1.json()
    assert body1["page"] == 1 and body1["page_size"] == 20
    assert body1["total_count"] == 25 and body1["total_pages"] == 2
    assert body1["total_amount"] == sum(range(1, 26))
    assert len(body1["entries"]) == 20
    # Newest first: day 25 → 6.
    assert body1["entries"][0]["amount"] == 25.0
    assert body1["entries"][-1]["amount"] == 6.0
    # Shape: only the three modal columns.
    assert set(body1["entries"][0].keys()) == {"date", "amount", "reason"}

    r2 = await client.get("/api/me/pvp-income?page=2", cookies=cookies)
    body2 = r2.json()
    assert body2["page"] == 2
    assert len(body2["entries"]) == 5
    # Same all-time stats regardless of page.
    assert body2["total_amount"] == body1["total_amount"]
    assert body2["total_count"] == 25


async def test_pvp_income_endpoint_out_of_range_page(client):
    await upsert_pvp_entries_batch(CID, [
        {"id": 1, "date": "2026-01-01T00:00:00Z", "amount": 5.0,
         "reason": "", "description": ""},
    ])
    cookies = await _user_cookie()
    r = await client.get("/api/me/pvp-income?page=999", cookies=cookies)
    body = r.json()
    assert body["entries"] == []
    assert body["total_count"] == 1
    assert body["total_amount"] == 5.0
    assert body["total_pages"] == 1


async def test_pvp_income_endpoint_clamps_negative_page(client):
    cookies = await _user_cookie()
    r = await client.get("/api/me/pvp-income?page=-3", cookies=cookies)
    assert r.json()["page"] == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `unset CORP_ID && pytest tests/test_pvp_income_history.py -v`
Expected: the 5 new tests FAIL — 404 (route doesn't exist yet).

- [ ] **Step 3: Add the endpoint to `main.py`**

In `main.py`, find `my_wallet` (the existing `@app.get("/api/me/wallet")` at line ~1302). Immediately **after** that route, insert:

```python


@app.get("/api/me/pvp-income")
async def my_pvp_income(page: int = 1, session: str | None = Cookie(None)):
    character_id = await get_current_character_id(session)
    page = max(1, page)
    page_size = 20
    entries, total = await database.list_pvp_entries_page(
        character_id, limit=page_size, offset=(page - 1) * page_size
    )
    total_pages = max(1, (total + page_size - 1) // page_size)
    total_amount = await database.sum_pvp_entries(character_id)
    return {
        "entries": entries,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "total_count": total,
        "total_amount": total_amount,
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `unset CORP_ID && pytest tests/test_pvp_income_history.py -v`
Expected: PASS — 16 passed total (8 + 3 + 5 new).

Run: `unset CORP_ID && pytest -q`
Expected: full suite green.

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_pvp_income_history.py
git commit -m "$(cat <<'EOF'
feat: GET /api/me/pvp-income paginated history endpoint

Page size 20, newest-first. Returns total_count + total_amount independent
of pagination so the modal can show a totals row.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Frontend — clickable metric + modal

**Files:**
- Modify: `static/index.html` (the PVP cell in `renderCharacterCard` near line 3134; new `openPVPIncomeModal` JS placed alongside `openProjectContributors` near line 1990; an ISK formatter helper if needed)

No automated frontend tests — verify via static checks.

- [ ] **Step 1: Make the PVP Income cell clickable**

In `static/index.html`, find the `pvpCell` definition inside `renderCharacterCard` (currently a const template literal around line 3134). It currently reads:

```javascript
    const pvpView = walletDisplay(pvp);
    const pvpCell = pvpErr
      ? `<div class="metric"><div class="label">PVP Income (30d)</div><div class="val">–</div></div>`
      : `<div class="metric">
           <div class="label">PVP Income (30d)</div>
           <div class="val ${pvpView.sizeCls}" title="Corp Account Withdrawal from Cerberus Agnon, last 30 days">
             ${pvpView.value}<span class="unit">ISK</span>
           </div>
         </div>`;
```

Change it to:

```javascript
    const pvpView = walletDisplay(pvp);
    const pvpCell = pvpErr
      ? `<div class="metric"><div class="label">PVP Income (30d)</div><div class="val">–</div></div>`
      : `<div class="metric" style="cursor:pointer" onclick="openPVPIncomeModal()"
              title="Click for full all-time history">
           <div class="label">PVP Income (30d)</div>
           <div class="val ${pvpView.sizeCls}">
             ${pvpView.value}<span class="unit">ISK</span>
           </div>
         </div>`;
```

(The `title` moves to the outer `.metric` so it shows on hover anywhere in the cell, including the label.)

- [ ] **Step 2: Add `openPVPIncomeModal` JS function**

In `static/index.html`, find `openProjectContributors` (around line 1990). Immediately **before** that function (so PVP-related logic lives next to the existing modal opener), insert:

```javascript
  async function openPVPIncomeModal(page = 1) {
    showModal('PVP Income — all-time history',
      '<div style="color:#475569;text-align:center;padding:1.5rem">Loading…</div>');
    try {
      const r = await fetch(`/api/me/pvp-income?page=${page}`);
      if (!r.ok) {
        setModalBody(cardErrorBlock(`Failed to load (${r.status})`));
        return;
      }
      const data = await r.json();
      setModalBody(renderPVPIncomeModal(data));
    } catch {
      setModalBody(cardErrorBlock("Couldn't load PVP income — try again."));
    }
  }

  function renderPVPIncomeModal(data) {
    const entries = data.entries || [];
    const tableBody = entries.length
      ? entries.map(e => `
          <tr>
            <td>${new Date(e.date).toLocaleString()}</td>
            <td style="text-align:right">${fmtISKLong(e.amount)} ISK</td>
            <td>${escHtml(e.reason || '–')}</td>
          </tr>`).join('')
      : `<tr><td colspan="3" style="color:#475569;text-align:center;padding:1rem">
           No PVP income recorded yet.
         </td></tr>`;
    const prevDisabled = data.page <= 1 ? 'disabled' : '';
    const nextDisabled = data.page >= data.total_pages ? 'disabled' : '';
    return `
      <table style="width:100%;border-collapse:collapse;font-size:0.85rem">
        <thead>
          <tr style="color:#64748b;text-transform:uppercase;font-size:0.7rem;letter-spacing:0.06em">
            <th style="text-align:left;padding:0.4rem 0.6rem">Date</th>
            <th style="text-align:right;padding:0.4rem 0.6rem">Amount</th>
            <th style="text-align:left;padding:0.4rem 0.6rem">Reason</th>
          </tr>
        </thead>
        <tbody>${tableBody}</tbody>
        <tfoot>
          <tr style="color:#94a3b8;font-size:0.8rem;border-top:1px solid #2d3348">
            <td style="padding:0.5rem 0.6rem" colspan="3">
              Total over ${fmtNum(data.total_count)} entries: ${fmtISKLong(data.total_amount)} ISK
            </td>
          </tr>
        </tfoot>
      </table>
      <div style="display:flex;justify-content:space-between;align-items:center;
                  margin-top:0.75rem;color:#64748b;font-size:0.75rem">
        <span>Page ${data.page} of ${data.total_pages}</span>
        <div style="display:flex;gap:0.4rem">
          <button class="btn-logout" ${prevDisabled}
                  onclick="openPVPIncomeModal(${data.page - 1})">Prev</button>
          <button class="btn-logout" ${nextDisabled}
                  onclick="openPVPIncomeModal(${data.page + 1})">Next</button>
        </div>
      </div>`;
  }
```

- [ ] **Step 3: Static verification**

Confirm via grep:
- `grep -n "openPVPIncomeModal" static/index.html` returns at least 3 hits (definition, onclick, next/prev buttons).
- `grep -n "renderPVPIncomeModal" static/index.html` returns 2 hits (definition + the call from `openPVPIncomeModal`).
- `grep -n "PVP Income — all-time history" static/index.html` returns the modal title.
- `grep -nc "Click for full all-time history" static/index.html` confirms the hover hint is wired.
- `grep -n "Total over" static/index.html` confirms the totals row text is present.

- [ ] **Step 4: Run the test suite**

Run: `unset CORP_ID && pytest -q`
Expected: PASS — no Python changes; ensures suite still green before commit.

- [ ] **Step 5: Self-review**

Re-read both new JS blocks:
- `openPVPIncomeModal(page=1)` opens the modal with a Loading message, then fetches `/api/me/pvp-income?page=N`, then renders via `setModalBody(renderPVPIncomeModal(data))`.
- `renderPVPIncomeModal(data)` builds: 3-column table (Date / Amount / Reason), tfoot totals row using `total_count` and `total_amount` (NOT the visible page), pager with prev/next buttons.
- Buttons re-enter `openPVPIncomeModal(N)` for the new page — the modal stays open and the body swaps.
- Empty state: tbody shows the "No PVP income recorded yet." row; totals row reads `Total over 0 entries: 0 ISK` (because `total_count=0` and `total_amount=0` flow through unchanged).
- Error state: `setModalBody(cardErrorBlock(...))` replaces the body; pager and totals disappear (acceptable — the error is the primary message).

- [ ] **Step 6: Commit**

```bash
git add static/index.html
git commit -m "$(cat <<'EOF'
feat: clickable PVP Income card opens all-time history modal

Renders newest-first paged table (date / amount / reason) plus a totals
row that stays constant across pagination. Reuses showModal/setModalBody.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Done

Four independent commits. After Task 4:

- `git log --oneline -5` shows the four feature commits plus the spec commit.
- `pytest -q` passes.
- Character card PVP Income cell is clickable; clicking opens the all-time history modal with date/amount/reason columns, pager, and totals row.
- All-time data accumulates from the first deploy of this feature forward (ESI's 30-day limit means earlier entries can't be backfilled).
